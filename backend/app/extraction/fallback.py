"""Multi-provider extraction architecture with fallback for CRE emails.

Pipeline:
Email → Gemini → Groq fallback → LocalExtractor fallback → Business logic.

If Gemini fails (timeout, rate limit, unconfigured), it falls back to Groq.
If Groq also fails or is unconfigured, it falls back to LocalExtractor.
Business logic performs final reconciliation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pydantic

from app.core.config import Settings, get_settings
from app.extraction.base import ExtractionError
from app.extraction.claude import SYSTEM_PROMPT, build_user_message
from app.extraction.local import LocalExtractor
from app.extraction.matching import DealCandidate
from app.extraction.schemas import EmailExtraction
from app.services.email_parser import ParsedEmail

log = logging.getLogger("app.extraction.fallback")


class GeminiExtractor:
    """Extracts structured CRE data via Google Gemini REST API."""

    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.gemini_model.strip() or "gemini-2.5-flash"
        self.timeout_seconds = self.settings.gemini_timeout_seconds
        self.http_client = http_client

    def is_configured(self) -> bool:
        key = self.settings.gemini_api_key
        return key is not None and bool(key.get_secret_value().strip())

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        if not self.is_configured():
            raise ExtractionError("not_configured", "Gemini API key is not configured")

        key = self.settings.gemini_api_key.get_secret_value().strip()  # type: ignore[union-attr]

        prompt = build_user_message(email, candidates)
        payload: dict[str, Any] = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }

        headers = {
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(self.timeout_seconds, connect=5.0)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        try:
            if self.http_client:
                resp = await self.http_client.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            log.warning("Gemini extractor model '%s' timed out after %.1fs: %s", self.model, self.timeout_seconds, exc)
            raise ExtractionError("timeout", f"Gemini extractor timed out after {self.timeout_seconds:.1f}s: {exc}") from exc
        except httpx.RequestError as exc:
            log.warning("Gemini extractor model '%s' connection error: %s", self.model, exc)
            raise ExtractionError("connection_error", f"Gemini connection error: {exc}") from exc

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            log.warning("Gemini extractor model '%s' returned HTTP %s: %s", self.model, status_code, exc)
            if status_code == 429:
                raise ExtractionError("rate_limited", f"Gemini quota exceeded (HTTP 429): {exc}") from exc
            if status_code in (401, 403):
                raise ExtractionError("auth_failed", f"Gemini authentication failed (HTTP {status_code}): {exc}") from exc
            raise ExtractionError("upstream_error", f"Gemini returned HTTP {status_code}: {exc}") from exc

        try:
            data = resp.json()
            candidates_out = data.get("candidates", [])
            if not candidates_out:
                raise ExtractionError("invalid_output", "Gemini returned no candidates")
            raw_text = candidates_out[0]["content"]["parts"][0]["text"].strip()
            return EmailExtraction.model_validate_json(raw_text)
        except ExtractionError:
            raise
        except (KeyError, IndexError, pydantic.ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise ExtractionError("invalid_output", f"Gemini response did not match extraction schema: {exc}") from exc


class GroqExtractor:
    """Extracts structured CRE data via Groq OpenAI-compatible REST API."""

    def __init__(self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.groq_model.strip() or "llama-3.3-70b-versatile"
        self.timeout_seconds = self.settings.groq_timeout_seconds
        self.http_client = http_client

    def is_configured(self) -> bool:
        key = self.settings.groq_api_key
        return key is not None and bool(key.get_secret_value().strip())

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        if not self.is_configured():
            raise ExtractionError("not_configured", "Groq API key is not configured")

        key = self.settings.groq_api_key.get_secret_value().strip()  # type: ignore[union-attr]
        url = "https://api.groq.com/openai/v1/chat/completions"

        prompt = build_user_message(email, candidates)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + "\nRespond with valid JSON adhering to the EmailExtraction schema."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)

        try:
            if self.http_client:
                resp = await self.http_client.post(url, json=payload, headers=headers, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            log.warning("Groq extractor model '%s' timed out after %.1fs: %s", self.model, self.timeout_seconds, exc)
            raise ExtractionError("timeout", f"Groq extractor timed out after {self.timeout_seconds:.1f}s: {exc}") from exc
        except httpx.RequestError as exc:
            log.warning("Groq extractor model '%s' connection error: %s", self.model, exc)
            raise ExtractionError("connection_error", f"Groq connection error: {exc}") from exc

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            log.warning("Groq extractor model '%s' returned HTTP %s: %s", self.model, status_code, exc)
            if status_code == 429:
                raise ExtractionError("rate_limited", f"Groq quota exceeded (HTTP 429): {exc}") from exc
            if status_code in (401, 403):
                raise ExtractionError("auth_failed", f"Groq authentication failed (HTTP {status_code}): {exc}") from exc
            raise ExtractionError("upstream_error", f"Groq returned HTTP {status_code}: {exc}") from exc

        try:
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise ExtractionError("invalid_output", "Groq returned no choices")
            raw_text = choices[0]["message"]["content"].strip()
            return EmailExtraction.model_validate_json(raw_text)
        except ExtractionError:
            raise
        except (KeyError, IndexError, pydantic.ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise ExtractionError("invalid_output", f"Groq response did not match extraction schema: {exc}") from exc


class FallbackExtractorRouter:
    """Orchestrates extraction: Gemini → Groq fallback → LocalExtractor fallback."""

    def __init__(
        self,
        gemini: GeminiExtractor | None = None,
        groq: GroqExtractor | None = None,
        local: LocalExtractor | None = None,
        primary: str = "gemini",
    ) -> None:
        self.gemini = gemini or GeminiExtractor()
        self.groq = groq or GroqExtractor()
        self.local = local or LocalExtractor()
        self.primary = primary
        self.model = "fallback-router"

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        providers = [("gemini", self.gemini), ("groq", self.groq)] if self.primary != "groq" else [("groq", self.groq)]

        for name, provider in providers:
            if provider.is_configured():
                try:
                    log.info("Attempting email extraction with %s (model=%s)", name.capitalize(), getattr(provider, "model", name))
                    result = await provider.extract(email, candidates)
                    self.model = getattr(provider, "model", name)
                    return result
                except Exception as exc:
                    log.warning(
                        "%s extraction failed (%s: %s) → trying next fallback",
                        name.capitalize(),
                        type(exc).__name__,
                        exc,
                    )
            else:
                log.info("%s not configured → trying next fallback", name.capitalize())

        log.info("Falling back to deterministic extraction (LocalExtractor)")
        self.model = "local-fallback"
        return await self.local.extract(email, candidates)
