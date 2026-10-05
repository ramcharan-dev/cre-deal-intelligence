"""Chooses the extraction provider from `LLM_PROVIDER`. Business logic only sees the `Extractor` interface.

Providers registered here take precedence; "claude" or "anthropic" routes to Anthropic.
If unspecified, defaults to `FallbackExtractorRouter` (Gemini → Groq → Local fallback).
"""

from collections.abc import Callable

from app.core.config import get_settings
from app.extraction import claude
from app.extraction.base import Extractor
from app.extraction.demo import DemoExtractor
from app.extraction.fallback import FallbackExtractorRouter, GeminiExtractor, GroqExtractor
from app.extraction.local import LocalExtractor

_PROVIDERS: dict[str, Callable[[], Extractor]] = {
    "demo": DemoExtractor,
    "local": LocalExtractor,
    "deterministic": LocalExtractor,
    "fallback": FallbackExtractorRouter,
    "gemini": lambda: FallbackExtractorRouter(primary="gemini"),
    "groq": lambda: FallbackExtractorRouter(primary="groq"),
}


def create_extractor(name: str | None = None) -> Extractor:
    provider = (get_settings().llm_provider if name is None else name).lower().strip()
    if provider in _PROVIDERS:
        return _PROVIDERS[provider]()
    return claude.get_extractor()




def get_extractor() -> Extractor:
    """FastAPI dependency for the configured extractor."""
    return create_extractor()
