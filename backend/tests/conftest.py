"""Test setup: point the app at a dedicated `<db>_test` database and migrate it.

Must run before any `app.*` import, because settings and the engine are created at import time.
"""

import os

import psycopg
import pytest
from psycopg import sql

os.environ["POSTGRES_DB"] = os.environ.get("POSTGRES_DB", "cre_deal_intel") + "_test"
os.environ["ANTHROPIC_API_KEY"] = ""  # never call the real API from tests
# Pin provider selection so a developer's .env (e.g. EXTRACTION_PROVIDER=ollama, LLM_PROVIDER=gemini, an explicit
# GOOGLE_REDIRECT_URI) can't route tests to real models or change the expected OAuth URLs.
os.environ["LLM_PROVIDER"] = ""
os.environ["EXTRACTION_PROVIDER"] = "claude"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["GOOGLE_REDIRECT_URI"] = ""

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.core.config import get_settings  # noqa: E402

TABLES = ("gmail_messages", "gmail_accounts", "extracted_values", "quotes", "emails", "lenders", "deals")


def _connect(dbname: str) -> psycopg.Connection:
    s = get_settings()
    return psycopg.connect(
        host=s.postgres_host,
        port=s.postgres_port,
        user=s.postgres_user,
        password=s.postgres_password.get_secret_value(),
        dbname=dbname,
        autocommit=True,
    )


@pytest.fixture(scope="session", autouse=True)
def test_database() -> None:
    name = get_settings().postgres_db
    with _connect("postgres") as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    command.upgrade(Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini")), "head")


@pytest.fixture
def clean_db(test_database) -> None:
    with _connect(get_settings().postgres_db) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
