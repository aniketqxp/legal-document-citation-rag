"""Tenant model.

Tenant is the root isolation entity — it has NO tenant_id field itself
because it IS the tenant. All other models reference Tenant.id as their
tenant_id foreign key.

In the MVP, one Tenant is created per user registration
(i.e., each lawyer gets their own isolated workspace).
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TenantCreate(SQLModel):
    name: str = Field(max_length=255, description="Human-readable workspace name")


class TenantPublic(SQLModel):
    id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime


# ── Database table ────────────────────────────────────────────────────────────

class Tenant(SQLModel, table=True):
    __tablename__ = "tenant"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=255, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    created_at: Optional[datetime] = Field(
        default_factory=_utc_now,
        nullable=False,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"server_default": "now()"},
    )
