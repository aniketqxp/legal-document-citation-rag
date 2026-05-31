"""Shared slowapi rate limiter instance.

Key function: uses the Authorization bearer token as the bucket key so each
authenticated user gets their own rate limit, regardless of shared IPs (e.g.
a whole law firm behind a single NAT gateway).  Falls back to the remote IP
for unauthenticated requests.
"""

from starlette.requests import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_user_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 10:
        return auth  # unique per user session
    return get_remote_address(request)


limiter = Limiter(key_func=_get_user_key)
