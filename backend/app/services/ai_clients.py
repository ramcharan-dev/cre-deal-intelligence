"""Lazily constructed clients for the AI providers.

Nothing here makes a network call on import or startup. Phase 2 only calls Anthropic; the Voyage and
Cohere factories exist for the retrieval phase and are not used by any request path yet.
"""

from functools import lru_cache

import anthropic
import cohere
import voyageai
from pydantic import SecretStr

from app.core.config import get_settings


class ProviderNotConfiguredError(RuntimeError):
    pass


def _configured(key: SecretStr | None) -> bool:
    return key is not None and bool(key.get_secret_value().strip())


def _require(key: SecretStr | None, name: str) -> str:
    if not _configured(key):
        raise ProviderNotConfiguredError(f"{name} is not set")
    return key.get_secret_value().strip()  # type: ignore[union-attr]


@lru_cache
def get_anthropic() -> anthropic.AsyncAnthropic:
    s = get_settings()
    headers = {"anthropic-workspace-id": s.anthropic_workspace_id} if s.anthropic_workspace_id else None
    return anthropic.AsyncAnthropic(
        api_key=_require(s.anthropic_api_key, "ANTHROPIC_API_KEY"),
        default_headers=headers,
        timeout=s.anthropic_timeout_seconds,
        max_retries=s.anthropic_max_retries,
    )


@lru_cache
def get_voyage() -> voyageai.AsyncClient:
    return voyageai.AsyncClient(api_key=_require(get_settings().voyage_api_key, "VOYAGE_API_KEY"))


@lru_cache
def get_cohere() -> cohere.AsyncClientV2:
    return cohere.AsyncClientV2(api_key=_require(get_settings().cohere_api_key, "COHERE_API_KEY"))


def provider_status() -> dict[str, dict[str, str | bool]]:
    """Which providers have credentials configured (no network calls)."""
    s = get_settings()

    claude = s.extraction_provider == "claude"

    active_provider = s.llm_provider.lower().strip() or "fallback"

    return {
        "gemini": {
            "configured": _configured(s.gemini_api_key),
            "model": s.gemini_model,
            "used_by": "email extraction & copilot" if active_provider in ("gemini", "fallback") else "copilot",
        },
        "groq": {
            "configured": _configured(s.groq_api_key),
            "model": s.groq_model,
            "used_by": "email extraction & copilot" if active_provider == "groq" else "email extraction fallback & copilot fallback",
        },
        "anthropic": {
            "configured": _configured(s.anthropic_api_key),
            "model": (
                s.llm_model
                if active_provider in ("anthropic", "claude") and s.llm_model.strip()
                else s.anthropic_model
            ),
            "used_by": (
                "email extraction"
                if claude
                else "not used (EXTRACTION_PROVIDER=ollama)"
            ),
        },
        "ollama": {
            "configured": not claude,
            "model": s.ollama_model,
            "used_by": (
                "email extraction"
                if not claude
                else "not used (EXTRACTION_PROVIDER=claude)"
            ),
        },
        "voyage": {
            "configured": _configured(s.voyage_api_key),
            "model": s.voyage_embedding_model,
            "used_by": "not used yet (retrieval phase)",
        },
        "cohere": {
            "configured": _configured(s.cohere_api_key),
            "model": s.cohere_rerank_model,
            "used_by": "not used yet (retrieval phase)",
        },
    }
