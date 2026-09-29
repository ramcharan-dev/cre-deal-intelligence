from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.routes import deals, emails, health
from app.core.config import get_settings
from app.core.logging import RequestLoggingMiddleware, configure_logging
from app.db.session import engine

settings = get_settings()
configure_logging(settings.log_level)

DESCRIPTION = """
Backend for the CRE AI Deal Intelligence POC.

**Email intelligence:** upload a broker/lender email; Claude extracts deal and lender-quote terms,
the email is matched to a deal (or a new one is created), and every stored value keeps a reference
to its source email and the verbatim text it came from.

**Errors** use one shape, `{"detail": {"code", "message", "retryable", "email_id", "request_id"}}`,
except FastAPI request-validation errors (422 with a list). Every response carries `X-Request-ID`.
"""

TAGS = [
    {"name": "emails", "description": "Upload emails and read extraction results."},
    {"name": "deals", "description": "Deals, lender quotes and per-field source references."},
    {
        "name": "health",
        "description": "Liveness and readiness (database, pgvector, migrations, provider keys).",
    },
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
    # Operation ids like `upload_email` instead of `upload_email_api_emails_post`.
    generate_unique_id_function=lambda route: route.name,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestLoggingMiddleware)  # outermost: logs every request, including CORS preflights
install_error_handlers(app)

app.include_router(emails.router, prefix="/api")
app.include_router(deals.router, prefix="/api")
app.include_router(health.router, prefix="/api")
