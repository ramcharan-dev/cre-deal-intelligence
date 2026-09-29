import time
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services.ai_clients import provider_status

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: str
    service: str
    environment: str


class DatabaseHealth(BaseModel):
    status: str
    latency_ms: float | None = None
    server_version: str | None = None
    pgvector_version: str | None = None
    alembic_revision: str | None = None
    error: str | None = None


class ProviderStatus(BaseModel):
    configured: bool = Field(description="An API key is set. The key is not validated here.")
    model: str
    used_by: str = Field(description="Which feature calls this provider")


class ReadinessResponse(BaseModel):
    status: str = Field(description="`ok`, or `degraded` when the database check fails (HTTP 503)")
    database: DatabaseHealth
    providers: dict[str, ProviderStatus]


@router.get("", response_model=LivenessResponse, summary="Liveness")
async def liveness() -> LivenessResponse:
    """Process is up. Does not touch dependencies."""
    s = get_settings()
    return LivenessResponse(status="ok", service=s.app_name, environment=s.environment)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness",
    responses={503: {"model": ReadinessResponse, "description": "Database unreachable or not migrated"}},
)
async def readiness(response: Response, db: Annotated[AsyncSession, Depends(get_db)]) -> ReadinessResponse:
    """Checks database connectivity, pgvector, and migration state."""
    db_health = await _check_database(db)
    ok = db_health.status == "ok"
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if ok else "degraded",
        database=db_health,
        providers=provider_status(),
    )


async def _check_database(db: AsyncSession) -> DatabaseHealth:
    try:
        start = time.perf_counter()
        await db.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        server_version = (await db.execute(text("SHOW server_version"))).scalar_one()
        pgvector_version = (
            await db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        ).scalar_one_or_none()
        alembic_revision = None
        if await _table_exists(db, "alembic_version"):
            alembic_revision = (
                await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 - health check reports any failure
        return DatabaseHealth(status="error", error=f"{type(exc).__name__}: {exc}")

    if pgvector_version is None:
        return DatabaseHealth(
            status="error",
            latency_ms=latency_ms,
            server_version=server_version,
            alembic_revision=alembic_revision,
            error="pgvector extension not installed (run `alembic upgrade head`)",
        )
    return DatabaseHealth(
        status="ok",
        latency_ms=latency_ms,
        server_version=server_version,
        pgvector_version=pgvector_version,
        alembic_revision=alembic_revision,
    )


async def _table_exists(db: AsyncSession, name: str) -> bool:
    result = await db.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name})
    return bool(result.scalar_one())
