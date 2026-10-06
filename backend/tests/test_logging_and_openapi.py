import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import logging as app_logging
from app.core.logging import RequestLoggingMiddleware, redact
from app.main import app

# ---------------------------------------------------------------- redaction


def test_redacts_provider_key_shapes() -> None:
    text = (
        "keys sk-ant-api03-abcdefghijklmnop and pa-abcdefghijklmnopqrstuvwxyz and Bearer abc.def-ghi_jkl123"
    )
    assert redact(text) == "keys [REDACTED] and [REDACTED] and [REDACTED]"


def test_redacts_google_token_shapes() -> None:
    text = (
        "access ya29.a0AfB_byC-abcdefghijkl refresh 1//0gAbCdEfGhIjKlMnOpQrStUv "
        "code 4/0AVHEtk7abcdefghijklmnopqrs secret GOCSPX-abcdefghijklmnop"
    )
    assert redact(text) == "access [REDACTED] refresh [REDACTED] code [REDACTED] secret [REDACTED]"


def test_redacts_configured_secrets(monkeypatch) -> None:
    class S:
        def secret_values(self) -> list[str]:
            return ["my-db-password-123"]

    monkeypatch.setattr(app_logging, "get_settings", lambda: S())
    assert redact("connect failed for my-db-password-123") == "connect failed for [REDACTED]"


def test_log_filter_redacts_formatted_messages() -> None:
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "auth with %s", ("sk-ant-api03-abcdefghijklmnop",), None
    )
    app_logging._ContextFilter().filter(record)
    assert record.getMessage() == "auth with [REDACTED]"
    assert record.request_id == "-"


# ---------------------------------------------------------------- request logging middleware


def _app_with(route) -> TestClient:
    mini = FastAPI()
    mini.add_middleware(RequestLoggingMiddleware)
    mini.add_api_route("/x", route, methods=["POST"])
    return TestClient(mini, raise_server_exceptions=False)


def test_logs_metadata_but_not_bodies_or_query(caplog) -> None:
    caplog.set_level(logging.INFO)

    async def echo(payload: dict) -> dict:
        return {"ok": True}

    r = _app_with(echo).post("/x?token=abc123", json={"body": "confidential-email-text"})
    assert r.status_code == 200 and len(r.headers["x-request-id"]) == 16
    line = next(rec.getMessage() for rec in caplog.records if rec.name == "app.request")
    assert line.startswith("POST /x -> 200 in ")
    assert "req 34B" in line
    assert "confidential" not in caplog.text and "abc123" not in caplog.text


def test_invalid_incoming_request_id_is_replaced() -> None:
    async def ok() -> dict:
        return {}

    r = _app_with(ok).post("/x", headers={"X-Request-ID": "bad id\nwith newline"})
    assert r.headers["x-request-id"] != "bad id\nwith newline"


def test_unhandled_exception_returns_json_500_without_leaking_message(caplog) -> None:
    async def boom() -> dict:
        raise ValueError(" ".join(["secret", "email", "body", "text"]))  # not a literal in the source frame

    r = _app_with(boom).post("/x")
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["code"] == "internal_error" and detail["request_id"] == r.headers["x-request-id"]
    assert "unhandled ValueError" in caplog.text
    assert "secret email body text" not in caplog.text and "secret email body text" not in r.text


# ---------------------------------------------------------------- OpenAPI


REDIRECT_OPERATIONS = {"oauth_start": "302", "oauth_callback": "303"}


def test_every_operation_is_documented() -> None:
    spec = app.openapi()
    ids = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            ids.append(op["operationId"])
            assert op.get("tags"), f"{method} {path} has no tag"
            assert op.get("summary"), f"{method} {path} has no summary"
            if "gmail" in op["tags"]:
                assert op.get("description"), f"{method} {path} has no description"
            if op["operationId"] in REDIRECT_OPERATIONS:
                assert REDIRECT_OPERATIONS[op["operationId"]] in op["responses"], path
                continue
            assert "$ref" in str(op["responses"]["200"]["content"]["application/json"]["schema"]), path
    assert len(ids) == len(set(ids)) == 18
    assert {t["name"] for t in spec["tags"]} == {"emails", "deals", "copilot", "gmail", "health"}


def test_upload_documents_multipart_body_and_errors() -> None:
    op = app.openapi()["paths"]["/api/emails"]["post"]
    body = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body == {"$ref": "#/components/schemas/EmailUploadForm"}
    for code in ("400", "413", "422", "429", "502", "503", "504"):
        assert op["responses"][code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_docs_are_served() -> None:
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 200
        assert c.get("/openapi.json").json()["info"]["title"] == "CRE AI Deal Intelligence"


def test_unknown_route_uses_error_shape() -> None:
    with TestClient(app) as c:
        r = c.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "http_error"
