"""Security utilities: JWT creation/decoding and Argon2 password hashing.

JWT payload structure:
    {
        "sub": "<user_uuid>",
        "tenant_id": "<tenant_uuid>",
        "exp": <unix_timestamp>
    }

The tenant_id in the JWT is the single source of truth for tenant isolation.
Every authenticated request carries it, and deps.py extracts it into CurrentUser.
"""

import uuid
from datetime import datetime, timedelta, timezone

from jwt import decode as jwt_decode
from jwt import encode as jwt_encode
from jwt.exceptions import InvalidTokenError  # noqa: F401 — re-exported for deps.py
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings

ALGORITHM = "HS256"

# Argon2id hasher — memory-hard, resistant to GPU/ASIC attacks
_password_hash = PasswordHash((Argon2Hasher(),))

# Pre-computed dummy hash for constant-time comparison on non-existent users
# (prevents user enumeration via timing attacks)
DUMMY_HASH = _password_hash.hash("dummy-timing-attack-prevention-password")


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    """Issue a signed JWT containing user identity and tenant isolation key."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "exp": expire,
    }
    return jwt_encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT. Raises jwt.InvalidTokenError on failure."""
    return jwt_decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


# ── Password hashing ──────────────────────────────────────────────────────────

def get_password_hash(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    """Verify a password and return (is_valid, updated_hash_or_None).

    The updated_hash is non-None when the hash needs rehashing (e.g. after
    an Argon2 parameter upgrade). The caller should persist the new hash.
    """
    return _password_hash.verify_and_update(plain_password, hashed_password)
