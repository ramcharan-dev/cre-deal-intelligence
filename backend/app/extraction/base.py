"""Model-independent extraction contract.

Business logic depends only on these names; providers (Gemini, Groq, Claude, the demo provider, and local
fallback) implement `Extractor`. They are currently defined in `claude.py` and re-exported here so
new code does not need to import a provider module.
"""

from app.extraction.claude import ErrorCode, ExtractionError, Extractor

__all__ = ["ErrorCode", "ExtractionError", "Extractor"]
