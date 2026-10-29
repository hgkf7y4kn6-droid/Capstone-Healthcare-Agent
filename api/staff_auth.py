"""
Staff identity: individual accounts, MFA, roles, idle timeout and revocation.

Accounts have an email, a scrypt-hashed password and a TOTP authenticator
(RFC 6238), and a role: admin, provider or front_desk. Sign-in returns an
opaque session cookie (httpOnly, SameSite=Strict, Secure in production); the
server keeps the session row, so sign-out, deactivation and "revoke all
sessions" take effect on the next request. A session ends after
STAFF_IDLE_MINUTES without use or SESSION_TTL_HOURS in total.

The shared clinic access code survives only for the synthetic-data demo:
it works when DEMO_MODE is on and nowhere else.

Clerk (or any hosted IdP) would replace `login` and the user table, not the
session or role checks in this file: routes depend on `require_staff` and
`require_role`, never on how someone proved who they are.
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import sys
import threading
import time
from collections import defaultdict, deque
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config
from data import audit_log, staff_store as store

ROLES = ("admin", "provider", "front_desk")
COOKIE = "aegis_staff"
CSRF_HEADER, CSRF_VALUE = "x-requested-with", "aegis"
MIN_PASSWORD = 12
LOCK_AFTER, LOCK_SECONDS = 5, 15 * 60
_bearer = HTTPBearer(auto_error=False)

_FAILED_WINDOW_S = 15 * 60
_FAILED_LIMIT = 8
_failed: dict[str, deque] = defaultdict(deque)
_failed_lock = threading.Lock()


# --------------------------------------------------------------------------- crypto helpers

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("not-a-real-password")  # burns the same time for unknown emails


def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_uri(email: str, secret: str) -> str:
    return f"otpauth://totp/{config.APP_NAME}:{email}?secret={secret}&issuer={config.APP_NAME}&digits=6&period=30"


def _totp(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    return f"{(struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** 6:06d}"


def verify_totp(secret: str, code: str, last_step: int, now: Optional[float] = None) -> Optional[int]:
    """Return the matching 30-second step, or None. Steps at or before the last
    accepted one are refused, so a code can't be replayed."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    current = int((now if now is not None else time.time()) // 30)
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(_totp(secret, step), code):
            return step
    return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- accounts

def create_account(email: str, name: str, role: str, password: str) -> dict:
    """Create a user and return it with the TOTP secret, shown once."""
    email = email.strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if "@" not in email or not name.strip():
        raise ValueError("an email and a name are required")
    secret = new_totp_secret()
    user_id = store.create_user(email, name.strip()[:60], role, hash_password(password), secret)
    return {"user_id": user_id, "email": email, "name": name.strip()[:60], "role": role,
            "totp_secret": secret, "totp_uri": totp_uri(email, secret)}


# --------------------------------------------------------------------------- throttling

def _throttled(client: str) -> bool:
    now = time.time()
    with _failed_lock:
        q = _failed[client]
        while q and now - q[0] > _FAILED_WINDOW_S:
            q.popleft()
        return len(q) >= _FAILED_LIMIT


def _record_failure(client: str) -> None:
    with _failed_lock:
        _failed[client].append(time.time())


# --------------------------------------------------------------------------- sessions

def _open_session(user: Optional[dict], name: str, role: str, demo: bool) -> dict:
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    expires = now + config.SESSION_TTL_HOURS * 3600
    store.create_session(_hash_token(token), secrets.token_hex(8), user["user_id"] if user else None,
                         name, role, demo, now, expires)
    store.purge_dead_sessions(now - 7 * 86400)
    return {"token": token, "name": name, "role": role, "expires_at": expires, "demo": demo,
            "idle_timeout_minutes": config.STAFF_IDLE_MINUTES}


def set_cookie(response: Response, session: dict) -> dict:
    """Put the token in an httpOnly cookie and keep it out of the response body."""
    response.set_cookie(COOKIE, session["token"], max_age=config.SESSION_TTL_HOURS * 3600, httponly=True,
                        samesite="strict", secure=config.is_production(), path="/")
    return {k: v for k, v in session.items() if k != "token"}


def clear_cookie(response: Response) -> None:
    response.set_cookie(COOKIE, "", max_age=0, expires=0, httponly=True, samesite="strict",
                        secure=config.is_production(), path="/")  # same attributes as set_cookie, so every browser clears it


def _guard(client: str) -> None:
    if not config.SESSION_SECRET:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Staff sign-in isn't configured. Set SESSION_SECRET.")
    if _throttled(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a few minutes.")


def login(email: str, password: str, otp: str, client: str) -> dict:
    """Email + password + authenticator code. Every failure reads the same."""
    _guard(client)
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "That email, password or code didn't match.")
    user = store.get_user_by_email((email or "").strip().lower())
    now = int(time.time())
    ok_password = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
    if not user or not user["active"] or user["locked_until"] > now:
        _record_failure(client)
        raise generic
    if not ok_password:
        store.record_login_failure(user["user_id"], LOCK_AFTER, now + LOCK_SECONDS)
        _record_failure(client)
        raise generic
    step = verify_totp(user["totp_secret"], otp, user["last_totp_step"])
    if step is None:
        store.record_login_failure(user["user_id"], LOCK_AFTER, now + LOCK_SECONDS)
        _record_failure(client)
        raise generic
    store.record_login_success(user["user_id"], step)
    return _open_session(user, user["name"], user["role"], demo=False)


def demo_sign_in(code: str, name: str, client: str) -> dict:
    """Shared code for the synthetic-data demo only."""
    if not config.DEMO_MODE or not config.STAFF_ACCESS_CODE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The shared access code is only available in demo mode.")
    _guard(client)
    if not hmac.compare_digest((code or "").strip().encode(), config.STAFF_ACCESS_CODE.encode()):
        _record_failure(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That access code didn't match.")
    return _open_session(None, (name or "").strip()[:60] or "Clinic team", "admin", demo=True)


def sign_out(token: str) -> None:
    store.revoke_session(_hash_token(token), int(time.time()))


def _resolve(token: str) -> dict:
    expired = HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session expired. Sign in again.")
    now = int(time.time())
    row = store.get_session(_hash_token(token))
    if not row or row["revoked_at"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    if row["expires_at"] <= now or now - row["last_seen"] > config.STAFF_IDLE_MINUTES * 60:
        raise expired
    if row["user_id"] is not None and not row["user_active"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    store.touch_session(_hash_token(token), now)
    role = row["user_role"] if row["user_id"] is not None else row["role"]  # a role change applies immediately
    name = row["user_name"] if row["user_id"] is not None else row["name"]
    return {"sub": name, "role": role, "exp": row["expires_at"], "session_id": row["session_id"],
            "user_id": row["user_id"], "demo": bool(row["demo"])}


def require_staff(request: Request, creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if creds:  # an explicit bearer token is used on its own, never mixed with the cookie
        if creds.scheme.lower() != "bearer":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
        token, via_cookie = creds.credentials, False
    else:
        token, via_cookie = request.cookies.get(COOKIE), True
    if not token or not config.SESSION_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    if via_cookie and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing request header.")
    claims = _resolve(token)
    claims["token"] = token
    request.state.staff = claims
    audit_log.record_request(request, claims["sub"], claims["role"])  # fails closed: no log, no data
    return claims


def require_role(*allowed: str):
    def dependency(staff: dict = Depends(require_staff)) -> dict:
        if staff["role"] not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can't do that.")
        return staff
    return dependency
