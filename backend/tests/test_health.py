"""Integration tests: require a reachable, migrated database (see README)."""

from fastapi.testclient import TestClient

from app.main import app


def test_liveness() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readiness_reports_db_and_pgvector() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health/ready")
    assert r.status_code == 200, r.text
    db = r.json()["database"]
    assert db["status"] == "ok"
    assert db["server_version"].startswith("18")
    assert db["pgvector_version"]
    assert db["alembic_revision"] == "0004"
