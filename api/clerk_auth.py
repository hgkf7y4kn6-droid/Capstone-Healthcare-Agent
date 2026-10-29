"""
Verify Clerk session tokens for staff sign-in.

Clerk handles the sign-in itself (email, password, required authenticator MFA,
invite-only sign-up, 15-minute inactivity limit, 8-hour maximum session). The
browser sends its short-lived (60 s) session token as a bearer token; this
module checks the RS256 signature against the issuer's public keys and reads
the claims our Clerk instance adds:

    role  = user public_metadata.role   (admin | provider | front_desk; set only from the Clerk dashboard or backend)
    name  = the user's full name

A user with no valid role is refused: being invited to Clerk is not the same as
being allowed into the chart. Revoking a session or removing a user in Clerk
stops new tokens at once; a token already issued lives out its 60 seconds.
"""

import base64
import os
import sys
from functools import lru_cache
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt
from fastapi import HTTPException, status

import config

ROLES = ("admin", "provider", "front_desk")


def configured() -> bool:
    return bool(config.CLERK_PUBLISHABLE_KEY)


@lru_cache(maxsize=4)
def _frontend_host(publishable_key: str) -> str:
    """pk_test_<base64 of 'host$'> -> host."""
    encoded = publishable_key.split("_", 2)[2]
    return base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")


def issuer() -> str:
    return f"https://{_frontend_host(config.CLERK_PUBLISHABLE_KEY)}"


@lru_cache(maxsize=1)
def _jwks_client() -> "jwt.PyJWKClient":
    return jwt.PyJWKClient(f"{issuer()}/.well-known/jwks.json", cache_keys=True, lifespan=3600, timeout=5)


def _signing_key(token: str):
    if config.CLERK_JWT_KEY:
        return config.CLERK_JWT_KEY
    return _jwks_client().get_signing_key_from_jwt(token).key


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2


def verify(token: str) -> dict:
    """Return {sub, name, role, session_id} for a valid staff token, else raise 401/403."""
    unauth = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    try:
        claims = jwt.decode(
            token, _signing_key(token), algorithms=["RS256"], issuer=issuer(),
            options={"require": ["exp", "iat", "nbf", "sub", "iss"], "verify_aud": False}, leeway=5)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session expired. Sign in again.")
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise unauth
    except Exception:  # JWKS unreachable: refuse rather than guess
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in is temporarily unavailable.")
    # azp is the origin that asked Clerk for the token; without this check a token minted for another site is accepted.
    azp: Optional[str] = claims.get("azp")
    if config.CLERK_AUTHORIZED_PARTIES and azp not in config.CLERK_AUTHORIZED_PARTIES:
        raise unauth
    if claims.get("sts") == "pending":  # signed in but hasn't finished MFA / required steps
        raise unauth
    role = claims.get("role")
    if role not in ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account hasn't been given a role yet. Ask an admin.")
    return {"sub": claims["sub"], "name": (claims.get("name") or "Staff")[:60], "role": role,
            "exp": claims["exp"], "session_id": claims.get("sid", ""), "user_id": None, "demo": False,
            "clerk": True}
