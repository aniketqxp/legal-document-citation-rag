"""Auth routes: login and register.

Login — POST /auth/login
  - Accepts OAuth2PasswordRequestForm (username=email, password=password)
  - Returns JWT access token + UserPublic

Register — POST /auth/register
  - Creates a new Tenant (workspace) + User in a single transaction
  - Returns JWT access token + UserPublic
  - Each registration creates an isolated workspace (one tenant per user for MVP)
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.api.deps import SessionDep
from app.core.security import create_access_token
from app.crud.tenant import create_tenant
from app.crud.user import authenticate, create_user, get_user_by_email
from app.models.tenant import TenantCreate
from app.models.user import UserPublic, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=Token)
async def login(
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    """Authenticate with email + password, receive a JWT.

    Uses OAuth2PasswordRequestForm so the OpenAPI /docs "Authorize" button works.
    The `username` field in the form is treated as the user's email address.
    """
    user = await authenticate(
        session, email=form_data.username, password=form_data.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    access_token = create_access_token(user.id, user.tenant_id)
    return Token(
        access_token=access_token,
        user=UserPublic.model_validate(user),
    )


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(
    session: SessionDep,
    user_in: UserRegister,
) -> Token:
    """Register a new account.

    Creates:
      1. A Tenant (isolated workspace named after the user)
      2. A User linked to that Tenant

    The JWT returned contains both user.id and tenant.id so every
    subsequent request is automatically scoped to the correct tenant.
    """
    existing = await get_user_by_email(session, user_in.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    # Derive a human-readable workspace name from the email
    workspace_name = (
        f"{user_in.full_name}'s Workspace"
        if user_in.full_name
        else f"{user_in.email.split('@')[0]}'s Workspace"
    )

    tenant = await create_tenant(session, TenantCreate(name=workspace_name))

    from app.models.user import UserCreate
    user = await create_user(
        session,
        UserCreate(
            email=user_in.email,
            password=user_in.password,
            full_name=user_in.full_name,
        ),
        tenant_id=tenant.id,
    )

    access_token = create_access_token(user.id, user.tenant_id)
    return Token(
        access_token=access_token,
        user=UserPublic.model_validate(user),
    )
