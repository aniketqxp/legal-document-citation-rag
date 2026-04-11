"""
TENANT ISOLATION CONTRACT
=========================
Every database table in this application MUST inherit from TenantBase.

RULES (violation = data breach):
  Rule 1 — Schema:  Every table has a non-nullable tenant_id column.
  Rule 2 — Writes:  tenant_id is always set from the JWT, never from user input.
  Rule 3 — Reads:   Every SELECT query MUST include WHERE tenant_id = :tenant_id.
  Rule 4 — Deletes: Soft or hard deletes MUST verify tenant_id before deletion.

These rules are enforced at the CRUD/service layer and tested in
tests/test_tenant_isolation.py.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TenantBase(SQLModel):
    """Base class for all tenant-isolated database tables.

    Provides: primary key, tenant isolation key, and audit timestamps.
    Do NOT instantiate directly — subclass with `table=True`.
    """

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        description="Surrogate primary key",
    )
    tenant_id: uuid.UUID = Field(
        index=True,
        nullable=False,
        description="Tenant isolation key. ALL queries MUST filter by this.",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), default=utc_now, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            default=utc_now,
            onupdate=utc_now,
            nullable=False,
        ),
    )
