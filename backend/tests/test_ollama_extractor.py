"""OllamaExtractor → /api/chat, with Ollama's HTTP API faked (no local model needed)."""

import json

import httpx
import pytest

from app.core.config import get_settings
from app.extraction.claude import ClaudeExtractor, ExtractionError, get_extractor
from app.extraction.ollama import OllamaExtractor
from app.services.email_parser import parse_email
from tests.test_email_ingestion import SUBMISSION, submission_extraction

EMAIL = parse_email(SUBMISSION)
VALID = submission_extraction(EMAIL, [])


def extractor(handler) -> OllamaExtractor:
    return OllamaExtractor(transport=httpx.MockTransport(handler))


def chat_response(content: str, done_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {"role": "assistant", "content": content},
            "done_reason": done_reason,
            "prompt_eval_count": 3100,
            "eval_count": 420,
        },
    )


@pytest.mark.anyio
async def test_sends_prompt_schema_and_context_size() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        assert request.url.path == "/api/chat"
        return chat_response(json.dumps(VALID))

    result = await extractor(handler).extract(EMAIL, [])
    assert result.email_type == "deal_submission"
    assert seen["model"] == get_settings().ollama_model and seen["stream"] is False
    assert seen["format"]["title"] == "EmailExtraction"
    assert seen["options"] == {"temperature": 0, "num_ctx": get_settings().ollama_num_ctx}
    assert seen["messages"][0]["role"] == "system" and "<email>" in seen["messages"][1]["content"]
    assert "240-unit multifamily" in seen["messages"][1]["content"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(404, json={"error": "model 'qwen2.5:7b' not found"}), "not_configured"),
        (httpx.Response(500, json={"error": "llama runner process has terminated"}), "upstream_error"),
        (chat_response("{}", done_reason="length"), "truncated"),
        (chat_response('{"email_type": "nonsense"}'), "invalid_output"),
        (chat_response("not json"), "invalid_output"),
    ],
)
async def test_errors_map_to_extraction_codes(response, code) -> None:
    with pytest.raises(ExtractionError) as info:
        await extractor(lambda _: response).extract(EMAIL, [])
    assert info.value.code == code


@pytest.mark.anyio
async def test_unreachable_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ExtractionError) as info:
        await extractor(handler).extract(EMAIL, [])
    assert info.value.code == "connection_error" and info.value.retryable
    assert "ollama serve" in info.value.message


def test_provider_selection(monkeypatch) -> None:
    assert isinstance(get_extractor(), ClaudeExtractor)
    monkeypatch.setattr(get_settings(), "extraction_provider", "ollama")
    selected = get_extractor()
    assert isinstance(selected, OllamaExtractor) and selected.model == f"ollama:{get_settings().ollama_model}"
