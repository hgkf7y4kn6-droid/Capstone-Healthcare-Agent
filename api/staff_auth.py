"""
Staff console sessions.

Until per-user identity (Clerk or Supabase Auth) is wired in, the console
uses a shared clinic access code exchanged for a short-lived signed session
token. Sessions carry a role so per-user auth can slot in later without
changing any route: routes depend on `require_staff`, never on the code.

Production refuses sign-in unless SESSION_SECRET and STAFF_ACCESS_CODE are
both set; failed attempts are throttled per client address.
"""

import hmac
import os
import sys
import threading
import time
from collections import defaultdict, deque

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config

ISSUER = "aegistriage"
AUDIENCE = "aegistriage-staff"
_bearer = HTTPBearer(auto_error=False)

_FAILED_WINDOW_S = 15 * 60
_FAILED_LIMIT = 8
_failed: dict[str, deque] = defaultdict(deque)
_failed_lock = threading.Lock()


def _configured() -> bool:
    return bool(config.SESSION_SECRET and config.STAFF_ACCESS_CODE)


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


def sign_in(code: str, name: str, client: str) -> dict:
    if not _configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Staff sign-in isn't configured. Set SESSION_SECRET and STAFF_ACCESS_CODE.")
    if _throttled(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a few minutes.")
    if not hmac.compare_digest((code or "").strip().encode(), config.STAFF_ACCESS_CODE.encode()):
        _record_failure(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That access code didn't match.")
    display = (name or "").strip()[:60] or "Clinic team"
    now = int(time.time())
    claims = {"sub": display, "role": "staff", "iss": ISSUER, "aud": AUDIENCE,
              "iat": now, "exp": now + config.SESSION_TTL_HOURS * 3600}
    token = jwt.encode(claims, config.SESSION_SECRET, algorithm="HS256")
    return {"token": token, "name": display, "role": "staff", "expires_at": claims["exp"]}


def decode(token: str) -> dict:
    try:
        return jwt.decode(token, config.SESSION_SECRET, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session expired. Sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")


def require_staff(request: Request, creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if not creds or creds.scheme.lower() != "bearer" or not _configured():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    claims = decode(creds.credentials)
    request.state.staff = claims
    return claims
