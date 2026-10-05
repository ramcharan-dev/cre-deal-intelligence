"""Load the bundled demo emails through the normal ingestion pipeline.

    python -m app.scripts.seed_demo                 # demo provider, no model calls
    python -m app.scripts.seed_demo --provider X    # any registered provider (see app.extraction.providers)

Emails are processed oldest first so thread replies find their deal. Already processed emails are skipped
(the pipeline returns the stored result), so the script is safe to re-run.
"""

import argparse
import asyncio
from pathlib import Path

from app.db.session import SessionLocal, engine
from app.extraction.base import Extractor
from app.extraction.demo import DEMO_DATA_DIR
from app.extraction.providers import create_extractor
from app.services.email_ingestion import IngestionError, ingest_email
from app.services.email_parser import parse_email


def demo_emails(data_dir: Path = DEMO_DATA_DIR) -> list[Path]:
    """Demo .eml files ordered by their Date header."""
    paths = sorted((data_dir / "emails").glob("*.eml"))
    return sorted(paths, key=lambda p: parse_email(p.read_bytes()).sent_at)


async def seed(extractor: Extractor, data_dir: Path = DEMO_DATA_DIR) -> list[tuple[str, str]]:
    """Ingest every demo email; returns (filename, outcome) pairs."""
    outcomes = []
    for path in demo_emails(data_dir):
        async with SessionLocal() as db:
            try:
                result = await ingest_email(db, path.read_bytes(), path.name, extractor)
            except IngestionError as exc:
                outcomes.append((path.name, f"failed: {exc.error.message}"))
                continue
        if result.duplicate:
            outcome = "already loaded"
        elif result.deal is None:
            outcome = "not a deal email"
        else:
            action = "new deal" if result.deal.created else f"matched by {result.deal.match_method}"
            outcome = f"{action} → {result.deal.deal_name} ({len(result.quotes)} quote(s))"
        outcomes.append((path.name, outcome))
    return outcomes


async def main(provider: str) -> None:
    try:
        for name, outcome in await seed(create_extractor(provider)):
            print(f"{name:40} {outcome}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--provider", default="demo", help="extraction provider (default: demo)")
    asyncio.run(main(parser.parse_args().provider))
