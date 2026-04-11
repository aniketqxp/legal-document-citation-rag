"""User model.

Table name is "app_user" to avoid conflict with PostgreSQL's
reserved word "user".

Users belong to a Tenant via tenant_id. The tenant_id embedded
in the JWT at login is extracted from this column.
"""

import uuid

from sqlmodel import Field, SQLModel

from app.models.base import TenantBase


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class UserBase(SQLModel):
    email: str = Field(max_length=255, unique=True, index=True)
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    is_superuser: bool = Field(default=False)


class UserCreate(UserBase):
    """Input for creating a user — includes plain-text password."""
    password: str = Field(min_length=8)


class UserRegister(SQLModel):
    """Input from the registration endpoint."""
    email: str = Field(max_length=255)
    password: str = Field(min_length=8)
    full_name: str | None = Field(default=None, max_length=255)


class UserUpdateMe(SQLModel):
    """Fields a user can update on their own profile (no privilege escalation)."""
    full_name: str | None = Field(default=None, max_length=255)


class UserPublic(UserBase):
    """Safe response schema — never exposes hashed_password."""
    id: uuid.UUID
    tenant_id: uuid.UUID


# ── Database table ────────────────────────────────────────────────────────────

class User(TenantBase, UserBase, table=True):
    __tablename__ = "app_user"

    hashed_password: str = Field(nullable=False, exclude=True)
