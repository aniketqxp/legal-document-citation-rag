"""User CRUD operations.

All read operations that are tenant-scoped include BOTH user_id AND tenant_id
in the WHERE clause — this dual-key lookup is the defence-in-depth mechanism
against any JWT token tampering that might substitute a valid user_id from
another tenant.

Adapted from: fastapi-full-stack-template/backend/app/crud.py
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.security import DUMMY_HASH, get_password_hash, verify_password
from app.models.user import User, UserCreate, UserUpdateMe


# ── Reads ─────────────────────────────────────────────────────────────────────

async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Look up a user by email (cross-tenant — used only during login/register)."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(
    session: AsyncSession,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> User | None:
    """Tenant-scoped user lookup. Requires BOTH user_id and tenant_id.

    The dual-key lookup prevents a tampered JWT (valid user_id, wrong tenant_id)
    from accessing another tenant's data.
    """
    result = await session.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


# ── Writes ────────────────────────────────────────────────────────────────────

async def create_user(
    session: AsyncSession,
    user_in: UserCreate,
    tenant_id: uuid.UUID,
) -> User:
    """Create a user. tenant_id comes from the server (never from user input)."""
    user = User.model_validate(
        user_in,
        update={
            "hashed_password": get_password_hash(user_in.password),
            "tenant_id": tenant_id,
        },
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(
    session: AsyncSession,
    email: str,
    password: str,
) -> User | None:
    """Verify email + password. Returns User on success, None on failure.

    Always performs a password verification (even for non-existent users)
    to prevent user enumeration via timing differences.
    """
    user = await get_user_by_email(session, email)
    if not user:
        # constant-time comparison against a dummy hash prevents user enumeration
        verify_password(password, DUMMY_HASH)
        return None

    verified, updated_hash = verify_password(password, user.hashed_password)
    if not verified:
        return None

    # Rehash if Argon2 parameters were upgraded (pwdlib automatic rehash)
    if updated_hash:
        user.hashed_password = updated_hash
        session.add(user)
        await session.commit()

    return user


async def update_user(
    session: AsyncSession,
    db_user: User,
    user_in: UserUpdateMe,
) -> User:
    """Update user profile fields (only those set by the caller)."""
    update_data = user_in.model_dump(exclude_unset=True)
    db_user.sqlmodel_update(update_data)
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user
