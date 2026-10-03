"""Ollama-backed email extraction: the same prompt and output schema as Claude, run on a local model.

Uses Ollama's structured outputs (`format` = the `EmailExtraction` JSON schema), so the response is parsed into
the same model and goes through the same validation, matching and persistence.
"""

import json
import logging
import time

import httpx
import pydantic

from app.core.config import get_settings
from app.core.logging import redact
from app.extraction.claude import SYSTEM_PROMPT, ExtractionError, build_user_message
from app.extraction.matching import DealCandidate
from app.extraction.schemas import EmailExtraction
from app.services.email_parser import ParsedEmail

log = logging.getLogger(__name__)


class OllamaExtractor:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        s = get_settings()
        self._model = s.ollama_model
        self.model = f"ollama:{s.ollama_model}"  # recorded on each email as the extraction model
        self._base_url = s.ollama_base_url.rstrip("/")
        self._timeout = s.ollama_timeout_seconds
        self._num_ctx = s.ollama_num_ctx
        self._transport = transport  # injected in tests

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(email, candidates)},
            ],
            "format": EmailExtraction.model_json_schema(),
            "stream": False,
            # The default context window is smaller than the prompt and would silently truncate the email.
            "options": {"temperature": 0, "num_ctx": self._num_ctx},
        }
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ExtractionError(
                "timeout", f"Ollama did not respond within {self._timeout:.0f}s; try again", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise ExtractionError(
                "connection_error",
                f"Could not reach Ollama at {self._base_url}. Is `ollama serve` running?",
                retryable=True,
            ) from exc

        if response.status_code == 404:
            raise ExtractionError(
                "not_configured",
                f"Ollama model '{self._model}' is not installed. Run `ollama pull {self._model}`.",
            )
        if not response.is_success:
            detail = redact(str(_error_text(response)))
            log.warning("ollama call failed status=%d", response.status_code)
            raise ExtractionError(
                "upstream_error",
                f"Ollama returned HTTP {response.status_code}: {detail}",
                retryable=response.status_code >= 500,
            )

        body = response.json()
        log.info(
            "ollama call ok model=%s done_reason=%s in=%s out=%s in %.1fs",
            self._model,
            body.get("done_reason"),
            body.get("prompt_eval_count"),
            body.get("eval_count"),
            time.perf_counter() - start,
        )
        if body.get("done_reason") == "length":
            raise ExtractionError("truncated", "Ollama's output was truncated; raise OLLAMA_NUM_CTX")
        content = (body.get("message") or {}).get("content") or ""
        try:
            return EmailExtraction.model_validate_json(content)
        except pydantic.ValidationError as exc:
            # The message would echo email content, so only the error count is logged.
            log.warning("ollama output failed schema validation (%d errors)", exc.error_count())
            raise ExtractionError(
                "invalid_output", f"{self._model}'s response did not match the extraction schema"
            ) from exc


def _error_text(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", ""))[:300]
    except (ValueError, AttributeError, json.JSONDecodeError):
        return f"HTTP {response.status_code}"
