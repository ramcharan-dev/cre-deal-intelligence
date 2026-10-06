"""The real ClaudeExtractor → API path, with only the Anthropic SDK client mocked (no API credits)."""

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pydantic
import pytest
from fastapi.testclient import TestClient

from app.api.errors import EXTRACTION_STATUS
from app.core.config import get_settings
from app.extraction.claude import ClaudeExtractor, ExtractionError, map_provider_error
from app.extraction.providers import get_extractor
from app.extraction.schemas import EmailExtraction
from app.main import app
from app.services import ai_clients
from app.services.email_parser import parse_email
from tests.test_email_ingestion import SUBMISSION, submission_extraction

FAKE_KEY = "sk-ant-api03-" + "x" * 40
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
EMAIL_SNIPPET = "240-unit multifamily property"  # appears only in the email body


def sdk_response(parsed: EmailExtraction | None, stop_reason: str = "end_turn") -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=2100, output_tokens=640, cache_read_input_tokens=1200, cache_creation_input_tokens=0
    )
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason=stop_reason,
        parsed_output=parsed,
        usage=usage,
        _request_id="req_ok123",
    )


def mock_client(result=None, error: Exception | None = None) -> SimpleNamespace:
    parse = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))


def status_error(cls, status: int, message: str, request_id: str = "req_err456"):
    response = httpx.Response(status, request=REQUEST, headers={"request-id": request_id})
    body = {"type": "error", "error": {"type": "some_error", "message": message}}
    return cls(f"Error code: {status} - {body}", response=response, body=body)


VALID = EmailExtraction.model_validate(submission_extraction(parse_email(SUBMISSION), []))


@pytest.fixture
def use_client(clean_db) -> Iterator:
    def install(client) -> TestClient:
        app.dependency_overrides[get_extractor] = lambda: ClaudeExtractor(client=client)
        return TestClient(app)

    yield install
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- request shape


@pytest.mark.anyio
async def test_request_uses_structured_output_thinking_caching_and_fallbacks() -> None:
    client = mock_client(sdk_response(VALID))
    parsed = parse_email(SUBMISSION)
    result = await ClaudeExtractor(client=client).extract(parsed, [])

    assert result is VALID
    kwargs = client.beta.messages.parse.await_args.kwargs
    assert kwargs["model"] == get_settings().anthropic_model == "claude-opus-5"
    assert kwargs["output_format"] is EmailExtraction
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": get_settings().anthropic_effort}
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    [message] = kwargs["messages"]
    assert message["role"] == "user"
    assert f"<email>\n{parsed.as_prompt_text()}\n</email>" in message["content"]
    assert "<existing_deals>\n[]\n</existing_deals>" in message["content"]


def test_runtime_dependency_is_the_real_claude_extractor() -> None:
    assert isinstance(get_extractor(), ClaudeExtractor)


def test_anthropic_client_gets_workspace_header_timeout_and_retries(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test123")
    get_settings.cache_clear()
    ai_clients.get_anthropic.cache_clear()
    try:
        client = ai_clients.get_anthropic()
        assert client.default_headers["anthropic-workspace-id"] == "wrkspc_test123"
        assert client.timeout == get_settings().anthropic_timeout_seconds
        assert client.max_retries == get_settings().anthropic_max_retries
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        ai_clients.get_anthropic.cache_clear()


# ---------------------------------------------------------------- full pipeline through the real extractor


def test_upload_through_real_extractor_persists_deal(use_client, caplog) -> None:
    caplog.set_level(logging.INFO)
    with use_client(mock_client(sdk_response(VALID))) as c:
        r = c.post(
            "/api/emails",
            files={"file": ("sub.eml", SUBMISSION, "message/rfc822")},
            headers={"X-Request-ID": "test-req-0001"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "claude-opus-5"
    assert body["deal"]["created"] is True
    assert r.headers["x-request-id"] == "test-req-0001"

    logs = caplog.text
    assert "claude call ok model=claude-opus-5" in logs and "upstream_request_id=req_ok123" in logs
    assert "POST /api/emails -> 200" in logs
    assert EMAIL_SNIPPET not in logs  # email content never logged
    assert "Parkside Apartments" not in logs


# ---------------------------------------------------------------- provider errors


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (status_error(anthropic.AuthenticationError, 401, "invalid x-api-key"), "auth_failed", False),
        (status_error(anthropic.PermissionDeniedError, 403, "not allowed"), "permission_denied", False),
        (
            status_error(anthropic.BadRequestError, 400, "must include the anthropic-workspace-id header"),
            "invalid_request",
            False,
        ),
        (status_error(anthropic.NotFoundError, 404, "model: claude-nope"), "invalid_request", False),
        (
            status_error(
                anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."
            ),
            "billing",
            False,
        ),
        (status_error(anthropic.RateLimitError, 429, "rate limited"), "rate_limited", True),
        (status_error(anthropic.OverloadedError, 529, "Overloaded"), "overloaded", True),
        (status_error(anthropic.InternalServerError, 500, "boom"), "upstream_error", True),
        (anthropic.APITimeoutError(request=REQUEST), "timeout", True),
        (anthropic.APIConnectionError(request=REQUEST), "connection_error", True),
    ],
)
def test_provider_errors_map_to_clean_http_errors(use_client, caplog, error, code, retryable) -> None:
    with use_client(mock_client(error=error)) as c:
        r = c.post("/api/emails", files={"file": ("sub.eml", SUBMISSION, "message/rfc822")})
    assert r.status_code == EXTRACTION_STATUS[code]
    detail = r.json()["detail"]
    assert (detail["code"], detail["retryable"]) == (code, retryable)
    assert detail["email_id"] and detail["request_id"] == r.headers["x-request-id"]
    assert "Error code:" not in detail["message"]  # the SDK's raw repr is never surfaced
    assert f"code={code}" in caplog.text


def test_api_error_detail_is_passed_through_for_bad_requests() -> None:
    err = map_provider_error(
        status_error(anthropic.BadRequestError, 400, "must include the anthropic-workspace-id header")
    )
    assert err.message == "Claude rejected the request (400): must include the anthropic-workspace-id header"
    assert err.upstream_request_id == "req_err456"


def test_secrets_echoed_by_the_api_are_redacted(caplog) -> None:
    err = map_provider_error(
        status_error(anthropic.PermissionDeniedError, 403, f"key {FAKE_KEY} lacks access")
    )
    assert FAKE_KEY not in err.message and "[REDACTED]" in err.message


def test_invalid_key_message_never_contains_the_key(use_client) -> None:
    error = status_error(anthropic.AuthenticationError, 401, f"invalid x-api-key {FAKE_KEY}")
    with use_client(mock_client(error=error)) as c:
        r = c.post("/api/emails", files={"file": ("sub.eml", SUBMISSION, "message/rfc822")})
    assert r.status_code == 503
    assert FAKE_KEY not in r.text
    assert r.json()["detail"]["message"] == "Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY."


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (sdk_response(VALID, stop_reason="refusal"), "refused"),
        (sdk_response(VALID, stop_reason="max_tokens"), "truncated"),
        (sdk_response(None), "invalid_output"),
    ],
)
async def test_unusable_responses_raise(response, code) -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await ClaudeExtractor(client=mock_client(response)).extract(parse_email(SUBMISSION), [])
    assert exc_info.value.code == code
    assert EXTRACTION_STATUS[code] == 422


@pytest.mark.anyio
async def test_schema_mismatch_is_invalid_output_and_not_logged(caplog) -> None:
    try:
        EmailExtraction.model_validate({"email_type": "deal_submission", "summary": EMAIL_SNIPPET})
    except pydantic.ValidationError as exc:
        validation_error = exc
    with pytest.raises(ExtractionError) as exc_info:
        await ClaudeExtractor(client=mock_client(error=validation_error)).extract(parse_email(SUBMISSION), [])
    assert exc_info.value.code == "invalid_output"
    assert EMAIL_SNIPPET not in caplog.text
