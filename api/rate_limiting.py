"""
Rate limiting for the portal-facing endpoints, using slowapi (a FastAPI-
friendly wrapper around limits). Keyed by the patient's token identity
(the 'sub' claim) rather than raw IP, since multiple patients can share a
NAT'd IP and a single compromised/misbehaving token is the actual thing we
want to throttle.

Degrades gracefully: if slowapi isn't installed, rate limiting is simply
disabled (logged once) rather than crashing the app — same pattern used
throughout this project for optional infra.
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    def _key_func(request):
        """Rate-limit key: the verified patient identity if the JWT was
        already decoded onto request.state by the auth dependency, else
        fall back to remote address (e.g. for unauthenticated attempts)."""
        patient_sub = getattr(request.state, "portal_patient_sub", None)
        return patient_sub or get_remote_address(request)

    limiter = Limiter(key_func=_key_func)
    SLOWAPI_AVAILABLE = True
except ImportError:
    limiter = None
    SLOWAPI_AVAILABLE = False
