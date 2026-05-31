"""FastAPI dependency injection for auth and database sessions.

Key pattern (adapted from fastapi-full-stack-template deps.py):
  - SessionDep: injects an async DB session scoped to the request
  - CurrentUser: decodes the JWT and returns the User ORM object with tenant_id
  - SuperUser: additionally asserts is_superuser=True

The tenant_id extracted from the JWT by get_current_user is the cornerstone
of tenant isolation. Every CRUD operation receives it from the CurrentUser object.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.core.db import async_session_maker
from app.models.user import User

# Points the OpenAPI "Authorize" button at the login endpoint
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)


# ── Database session ──────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


# ── Authentication + Tenant Extraction ────────────────────────────────────────

async def get_current_user(session: SessionDep, token: TokenDep) -> User:
    """Decode JWT → extract user_id + tenant_id → load User from DB.

    The tenant_id in the returned User object is the single source of truth
    for all downstream CRUD and search operations.
    """
    # Import here to avoid model import cycles
    from app.crud.user import get_user_by_id

    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = security.decode_access_token(token)
        user_id_str: str | None = payload.get("sub")
        tenant_id_str: str | None = payload.get("tenant_id")
        if not user_id_str or not tenant_id_str:
            raise credentials_exc
        user_id = uuid.UUID(user_id_str)
        tenant_id = uuid.UUID(tenant_id_str)
    except (InvalidTokenError, ValueError):
        raise credentials_exc

    user = await get_user_by_id(session, user_id=user_id, tenant_id=tenant_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or session expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient privileges",
        )
    return current_user


SuperUser = Annotated[User, Depends(get_current_active_superuser)]
