"""Deterministic demo provider for the POC: no model is called.

It replays hand-written extractions for the bundled demo emails (`backend/demo_data`), keyed by
Message-ID. The output goes through the same validation → deal matching → persistence pipeline as a real
model's, so every value still needs a verbatim `source_text` that occurs in the email.
"""

import json
from functools import lru_cache
from pathlib import Path

from app.extraction.base import ExtractionError
from app.extraction.matching import DealCandidate
from app.extraction.schemas import EmailExtraction
from app.services.email_parser import ParsedEmail

from app.extraction.fallback import FallbackExtractorRouter
from app.extraction.local import LocalExtractor

DEMO_DATA_DIR = Path(__file__).resolve().parents[2] / "demo_data"


@lru_cache
def load_fixtures(data_dir: Path = DEMO_DATA_DIR) -> dict[str, EmailExtraction]:
    """Message-ID → extraction, from `<data_dir>/extractions/*.json` (each file carries `message_id`)."""
    fixtures = {}
    for path in sorted((data_dir / "extractions").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        fixtures[data["message_id"]] = EmailExtraction.model_validate(data)
    return fixtures


class DemoExtractor:
    model = "demo-fixtures"

    def __init__(self, data_dir: Path = DEMO_DATA_DIR, strict: bool = False) -> None:
        self._data_dir = data_dir
        self.strict = strict
        self._fallback = FallbackExtractorRouter()

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        fixture = load_fixtures(self._data_dir).get(email.message_id or "")
        if fixture is not None:
            self.model = "demo-fixtures"
            return fixture.model_copy(deep=True)
        if self.strict:
            raise ExtractionError(
                "not_configured",
                "The demo provider only knows the bundled demo emails (backend/demo_data/emails). "
                "Configure a model provider (LLM_PROVIDER) to process other emails.",
            )
        result = await self._fallback.extract(email, candidates)
        self.model = self._fallback.model
        return result

