"""Chooses the extraction provider from `LLM_PROVIDER`. Business logic only sees the `Extractor` interface.

Providers registered here take precedence. Anything else (empty, "claude", "anthropic") defers to
`EXTRACTION_PROVIDER` via `claude.get_extractor`, which returns Claude or Ollama.
"""

from collections.abc import Callable

from app.core.config import get_settings
from app.extraction import claude
from app.extraction.base import Extractor
from app.extraction.demo import DemoExtractor
from app.extraction.fallback import FallbackExtractorRouter
from app.extraction.local import LocalExtractor


def _ollama() -> Extractor:
    from app.extraction.ollama import OllamaExtractor  # imports claude, so import lazily

    return OllamaExtractor()


_PROVIDERS: dict[str, Callable[[], Extractor]] = {
    "demo": DemoExtractor,
    "local": LocalExtractor,
    "deterministic": LocalExtractor,
    "fallback": FallbackExtractorRouter,
    "gemini": lambda: FallbackExtractorRouter(primary="gemini"),
    "groq": lambda: FallbackExtractorRouter(primary="groq"),
    "ollama": _ollama,
}


def create_extractor(name: str | None = None) -> Extractor:
    provider = (get_settings().llm_provider if name is None else name).lower().strip()
    if provider in _PROVIDERS:
        return _PROVIDERS[provider]()
    return claude.get_extractor()


def get_extractor() -> Extractor:
    """FastAPI dependency for the configured extractor."""
    return create_extractor()
