from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Health check")
async def health_check(session: SessionDep) -> dict:
    """Returns service health and database connectivity status.

    Used by Docker Compose healthcheck:
        test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
    """
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unreachable",
    }
