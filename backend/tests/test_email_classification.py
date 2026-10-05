"""Tests for email classification and extraction fallback.

Verifies:
1. Broker financing mandates are classified as 'deal_submission'.
2. Lender responses containing financing terms are classified as 'lender_quote'.
3. Lender decline emails are classified as 'lender_quote' with quote_status='declined'.
4. Newsletters and market commentary are classified as 'other'.
5. Multi-field lender quote term extraction (rate, LTV, DSCR, spread, term, amort, IO, etc.).
6. Subject prefixes (RE:, FW:) and quoted reply history stripping.
7. FallbackExtractorRouter fallback sequence (Gemini -> Groq -> LocalExtractor).
8. Business logic reconciliation guardrail via reconcile_email_type().
"""

import asyncio

import pytest

from app.extraction.fallback import FallbackExtractorRouter, GeminiExtractor, GroqExtractor
from app.extraction.local import LocalExtractor
from app.extraction.matching import DealCandidate
from app.extraction.schemas import SourcedText, ValidatedField, ValidatedLenderQuote
from app.extraction.validation import validate_extraction
from app.services.email_ingestion import reconcile_email_type
from app.services.email_parser import parse_email
from tests.emails import make_eml


def test_broker_mandate_classified_as_deal_submission() -> None:
    raw = make_eml(
        "New Acquisition Financing - Phoenix Gateway Industrial - $28MM",
        "Team,\n\n"
        "We have been engaged to arrange acquisition financing for Phoenix Gateway Industrial.\n\n"
        "Property: Phoenix Gateway Industrial\n"
        "Location: 4500 E Cotton Center Blvd, Phoenix, AZ 85040\n"
        "Property Type: Industrial warehouse / distribution center\n"
        "Size: 320,000 SF\n"
        "Occupancy: 94% occupied\n"
        "Purchase Price: $40,000,000\n"
        "Loan Request: $28,000,000 senior debt\n"
        "Target Closing: 2026-11-15\n"
        "Sponsor: Sunbelt Logistics Partners LLC\n\n"
        "Please let us know if you can quote.\n",
        sender="Marcus Vance <mvance@apexbrokerage.com>",
        message_id="<mandate-phoenix-001@apexbrokerage.com>",
    )
    email = parse_email(raw)
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, []))

    assert extraction.email_type == "deal_submission"
    assert "Phoenix Gateway" in extraction.summary
    assert len(extraction.quotes) == 0

    validated = validate_extraction(extraction, email.as_prompt_text(), set())
    assert validated.issues == []


def test_broker_refinance_mandate_classified_as_deal_submission() -> None:
    raw = make_eml(
        "New Refinance Opportunity - Austin Business Center - $22MM",
        "We are pleased to introduce a new refi mandate:\n\n"
        "Property: Austin Business Center, Austin, TX\n"
        "Office / flex | 150,000 SF | 88% occupancy\n"
        "Loan request: $22,000,000\n"
        "Sponsor: Lone Star Capital LLC\n",
        sender="David Wright <dwright@wrightcap.com>",
        message_id="<mandate-austin-002@wrightcap.com>",
    )
    email = parse_email(raw)
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, []))

    assert extraction.email_type == "deal_submission"
    assert len(extraction.quotes) == 0


def test_lender_quote_reply_with_re_subject_classified_as_lender_quote() -> None:
    raw = make_eml(
        "RE: New Acquisition Financing - Phoenix Gateway Industrial - $28MM",
        "Marcus,\n\n"
        "Northmark Capital is pleased to offer the following loan terms for Phoenix Gateway Industrial:\n\n"
        "Lender: Northmark Capital\n"
        "Loan Amount: $28,000,000\n"
        "Interest Rate: 5.35%\n"
        "Rate Type: Fixed\n"
        "Spread: 165 bps over 5Y UST\n"
        "Term: 5 years\n"
        "Amortization: 30 years\n"
        "Interest-Only: 2 years IO\n"
        "LTV: 70%\n"
        "Min DSCR: 1.25x\n"
        "Origination Fee: 0.50%\n"
        "Recourse: Non-recourse\n\n"
        "Best regards,\n"
        "David Cole\n\n"
        "-----Original Message-----\n"
        "From: Marcus Vance <mvance@apexbrokerage.com>\n"
        "Sent: Monday, October 1, 2026\n"
        "Subject: New Acquisition Financing - Phoenix Gateway Industrial - $28MM\n\n"
        "We have been engaged to arrange acquisition financing for Phoenix Gateway Industrial...\n",
        sender="David Cole <dcole@northmarkcap.com>",
        message_id="<quote-northmark-003@northmarkcap.com>",
    )
    email = parse_email(raw)
    candidate = DealCandidate(id="deal-phx", deal_name="Phoenix Gateway Industrial", property_name="Phoenix Gateway Industrial")
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, [candidate]))

    assert extraction.email_type == "lender_quote"
    assert extraction.deal_match.matched_deal_id == "deal-phx"
    assert len(extraction.quotes) >= 1

    quote = extraction.quotes[0]
    qfields = {f.field: str(f.value) for f in quote.fields}
    assert "Northmark" in quote.lender_name.value
    assert qfields.get("interest_rate") == "5.35"
    assert qfields.get("rate_type") == "fixed"
    assert qfields.get("spread_bps") == "165"
    assert qfields.get("ltv") == "70"
    assert qfields.get("min_dscr") == "1.25"
    assert qfields.get("term_months") == "60"  # 5 years * 12
    assert qfields.get("amortization_months") == "360"  # 30 years * 12
    assert qfields.get("interest_only_months") == "24"  # 2 years * 12
    assert qfields.get("recourse") == "non_recourse"
    assert qfields.get("origination_fee_pct") == "0.50"

    validated = validate_extraction(extraction, email.as_prompt_text(), {"deal-phx"})
    assert validated.issues == []


def test_lender_financing_proposal_subject_classified_as_lender_quote() -> None:
    raw = make_eml(
        "RE: Phoenix Gateway Industrial - Financing Proposal",
        "Hi Marcus,\n\n"
        "Pacific Commercial Finance can offer floating rate debt:\n\n"
        "Loan amount: $27,500,000\n"
        "Index: 1M Term SOFR\n"
        "Spread: 225 bps\n"
        "Rate type: floating\n"
        "LTV: 68%\n"
        "Term: 3 years\n"
        "Full-recourse\n\n"
        "> Original inquiry regarding Phoenix Gateway Industrial financing\n",
        sender="Sarah Lin <slin@pacificcomfinance.com>",
        message_id="<prop-pacific-004@pacificcomfinance.com>",
    )
    email = parse_email(raw)
    candidate = DealCandidate(id="deal-phx", deal_name="Phoenix Gateway Industrial", property_name="Phoenix Gateway Industrial")
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, [candidate]))

    assert extraction.email_type == "lender_quote"
    assert len(extraction.quotes) >= 1
    quote = extraction.quotes[0]
    qfields = {f.field: str(f.value) for f in quote.fields}
    assert qfields.get("rate_type") == "floating"
    assert qfields.get("spread_bps") == "225"
    assert qfields.get("ltv") == "68"
    assert qfields.get("recourse") == "full_recourse"

    validated = validate_extraction(extraction, email.as_prompt_text(), {"deal-phx"})
    assert validated.issues == []


def test_lender_decline_classified_as_lender_quote_with_declined_status() -> None:
    raw = make_eml(
        "RE: New Acquisition Financing - Phoenix Gateway Industrial - $28MM",
        "Hi Marcus,\n\n"
        "Thanks for sending over Phoenix Gateway Industrial. Unfortunately, our credit team passed on this deal "
        "as we are currently out of industrial in the Southwest.\n\n"
        "Regards,\n"
        "Tom Higgins\n"
        "Apex Debt Partners\n\n"
        "-----Original Message-----\n"
        "From: Marcus Vance\n"
        "Subject: New Acquisition Financing - Phoenix Gateway Industrial - $28MM\n",
        sender="Tom Higgins <thiggins@apexdebtpartners.com>",
        message_id="<decline-apex-005@apexdebtpartners.com>",
    )
    email = parse_email(raw)
    candidate = DealCandidate(id="deal-phx", deal_name="Phoenix Gateway Industrial", property_name="Phoenix Gateway Industrial")
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, [candidate]))

    assert extraction.email_type == "lender_quote"
    assert extraction.deal_match.matched_deal_id == "deal-phx"
    assert len(extraction.quotes) >= 1
    quote = extraction.quotes[0]
    qfields = {f.field: str(f.value) for f in quote.fields}
    assert qfields.get("quote_status") == "declined"
    assert "declined" in extraction.summary.lower()

    validated = validate_extraction(extraction, email.as_prompt_text(), {"deal-phx"})
    assert validated.issues == []


def test_lender_unable_to_quote_decline() -> None:
    raw = make_eml(
        "RE: The Lofts at Riverbend - Refinance",
        "Marcus, we are unable to quote at this time; we passed on this opportunity.\n\n"
        "Beacon Agency Lending\n",
        sender="Laura Chen <lchen@beaconagency.com>",
        message_id="<decline-beacon-006@beaconagency.com>",
    )
    email = parse_email(raw)
    candidate = DealCandidate(id="deal-rb", deal_name="The Lofts at Riverbend", property_name="The Lofts at Riverbend")
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, [candidate]))

    assert extraction.email_type == "lender_quote"
    assert len(extraction.quotes) >= 1
    assert any(f.field == "quote_status" and f.value == "declined" for f in extraction.quotes[0].fields)


def test_newsletter_classified_as_other() -> None:
    raw = make_eml(
        "CRE Weekly Market Report: 10Y UST and Cap Rate Trends",
        "Good morning colleagues,\n\n"
        "Here is your weekly market summary:\n"
        "The 10-Year Treasury closed the week at 4.15%. Industrial absorption remains healthy across primary markets.\n"
        "Multifamily transaction volume is expected to rise into Q4.\n\n"
        "Click here to unsubscribe.\n",
        sender="Research Dept <newsletter@cre-analytics.com>",
        message_id="<news-007@cre-analytics.com>",
    )
    email = parse_email(raw)
    extractor = LocalExtractor()
    extraction = asyncio.run(extractor.extract(email, []))

    assert extraction.email_type == "other"
    assert len(extraction.quotes) == 0


def test_business_logic_reconcile_email_type_guardrail() -> None:
    # 1. Extractor returned 'deal_submission' by mistake, but quotes are present -> coerced to 'lender_quote'
    dummy_quote = ValidatedLenderQuote(
        lender_name="Bank of America",
        lender_name_source="Bank of America",
        contact_name=None,
        contact_email=None,
        option_label=None,
        fields=[ValidatedField(field="interest_rate", raw_value="5.50", value="5.50", source_text="5.50%")],
    )
    result = reconcile_email_type(
        email_type="deal_submission",
        quotes=[dummy_quote],
        deal_fields=[],
        body_text="Here are the indicative terms for the loan: 5.50%",
    )
    assert result == "lender_quote"

    # 2. Extractor returned 'deal_submission', but body has explicit decline -> coerced to 'lender_quote'
    result_decline = reconcile_email_type(
        email_type="deal_submission",
        quotes=[],
        deal_fields=[],
        body_text="We reviewed the deal and our credit team passed.",
    )
    assert result_decline == "lender_quote"

    # 3. Deal closing update -> coerced to 'deal_update'
    result_closing = reconcile_email_type(
        email_type="other",
        quotes=[],
        deal_fields=[],
        body_text="Great news, closing confirmed for Friday, loan funded.",
    )
    assert result_closing == "deal_update"

    # 4. Clean submission with no quotes -> remains 'deal_submission'
    result_sub = reconcile_email_type(
        email_type="deal_submission",
        quotes=[],
        deal_fields=[],
        body_text="Here is a new financing mandate for Riverbend.",
    )
    assert result_sub == "deal_submission"


def test_fallback_router_uses_local_when_keys_empty() -> None:
    router = FallbackExtractorRouter()
    assert not router.gemini.is_configured()
    assert not router.groq.is_configured()

    raw = make_eml(
        "New Acquisition Financing - Harbor Point Logistics Center - $31MM",
        "Property: Harbor Point Logistics Center\n"
        "Location: Savannah, GA\n"
        "Loan request: $31,000,000\n"
        "Industrial warehouse | 350,000 SF\n",
        sender="Marcus Vance <mvance@apexbrokerage.com>",
        message_id="<mandate-harbor-008@apexbrokerage.com>",
    )
    email = parse_email(raw)
    result = asyncio.run(router.extract(email, []))

    assert result.email_type == "deal_submission"
    assert router.model == "local-fallback"
