"""Tenant CRUD operations.

Tenants are the root isolation entity. There are very few operations here —
tenants are created once on registration and rarely mutated.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant, TenantCreate


async def create_tenant(session: AsyncSession, tenant_in: TenantCreate) -> Tenant:
    """Create and persist a new tenant (workspace)."""
    tenant = Tenant(**tenant_in.model_dump())
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant
