"""Unit tests for deterministic LocalExtractor."""

import asyncio

from app.extraction.local import LocalExtractor
from app.extraction.matching import DealCandidate
from app.extraction.validation import validate_extraction
from app.services.email_parser import parse_email
from tests.emails import make_eml


def test_extract_deal_submission() -> None:
    raw = make_eml(
        "New refi mandate - The Oaks at Westlake (Austin MF) - $25MM",
        "Property: The Oaks at Westlake, 1000 Westlake Dr, Austin, TX\n"
        "Multifamily | 160 units | Built in 2019 | 95% occupancy\n"
        "Loan request: $25,000,000 refi\n"
        "Sponsor: Lone Star Holdings\n"
        "Target closing: 2026-12-01\n",
        message_id="<sub-100@broker.com>",
    )
    email = parse_email(raw)
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, []))

    assert extraction.email_type == "deal_submission"
    assert extraction.deal_match.matched_deal_id is None

    # Validate that every source_text is valid
    validated = validate_extraction(extraction, email.as_prompt_text(), set())
    assert validated.issues == []

    fields = {f.field: f.value for f in validated.deal_fields}
    assert "The Oaks at Westlake" in str(fields.get("deal_name"))
    assert fields.get("units") == 160
    assert fields.get("year_built") == 2019
    assert fields.get("property_type") == "multifamily"


def test_extract_lender_quote() -> None:
    raw = make_eml(
        "RE: New refi mandate - The Oaks at Westlake",
        "Laura Chen <lchen@beaconagency.com> writes:\n\n"
        "Beacon Agency Lending is pleased to provide indicative terms:\n"
        "Loan amount: $25,000,000\n"
        "Rate: 10-year fixed at 5.25%\n"
        "Amortization: 30 years with 3 years interest-only\n"
        "LTV: 65%\n"
        "Min DSCR: 1.25x\n"
        "Origination fee: 0.50%\n"
        "Non-recourse\n",
        message_id="<quote-100@beaconagency.com>",
    )
    email = parse_email(raw)
    extractor = LocalExtractor()
    extraction = asyncio.run(
        extractor.extract(
            email,
            [
                DealCandidate(
                    id="deal-1", deal_name="The Oaks at Westlake", property_name="The Oaks at Westlake"
                )
            ],
        )
    )

    assert extraction.email_type == "lender_quote"
    assert extraction.deal_match.matched_deal_id == "deal-1"

    validated = validate_extraction(extraction, email.as_prompt_text(), {"deal-1"})
    assert validated.issues == []
    assert len(validated.quotes) == 1
    quote = validated.quotes[0]
    assert "Beacon" in quote.lender_name
    qfields = {f.field: str(f.value) for f in quote.fields}
    assert qfields.get("rate_type") == "fixed"
    assert qfields.get("interest_rate") == "5.25"
