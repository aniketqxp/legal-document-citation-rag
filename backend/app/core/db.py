from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# ── Async engine — used by the FastAPI app and Celery workers ──────────────────
engine = create_async_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    echo=settings.ENVIRONMENT == "local",
    pool_pre_ping=True,     # reconnect on stale connections
    pool_size=5,
    max_overflow=10,
)

# Session factory — use as an async context manager
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep ORM objects usable after commit
)


async def init_db() -> None:
    """Seed the initial superuser if it doesn't exist yet.

    Called from app.initial_data after Alembic migrations have run.
    Migrations create the schema; this creates the first data row.
    """
    # Import here to avoid circular imports at module load time
    from app.crud.user import create_user, get_user_by_email
    from app.crud.tenant import create_tenant
    from app.models.user import UserCreate
    from app.models.tenant import TenantCreate

    async with async_session_maker() as session:
        existing = await get_user_by_email(session, settings.FIRST_SUPERUSER)
        if not existing:
            tenant = await create_tenant(
                session,
                TenantCreate(name="Harvey Admin Workspace"),
            )
            user_in = UserCreate(
                email=settings.FIRST_SUPERUSER,
                password=settings.FIRST_SUPERUSER_PASSWORD,
                full_name="Harvey Admin",
                is_superuser=True,
            )
            await create_user(session, user_in, tenant_id=tenant.id)
