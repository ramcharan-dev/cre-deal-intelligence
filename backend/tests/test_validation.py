from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.extraction.fields import DEAL_FIELD_SPECS, DEAL_FIELDS, QUOTE_FIELD_SPECS, QUOTE_FIELDS
from app.extraction.schemas import EmailExtraction
from app.extraction.validation import (
    CoercionError,
    coerce_value,
    normalize_for_match,
    source_in_email,
    validate_extraction,
)
from app.models import Deal, Quote

EMAIL = """From: Jane Broker <jane@brokerco.com>
Subject: 123 Main St - refi

Hi team,
We're seeking a $12.5MM refinance for Parkside Apartments, 240 units at
123 Main Street, Austin TX. Occupancy is 94.5%.

> On Mon, Wells Fargo wrote:
> We can offer 65% LTV at S+275, 3 yr term.
"""


def extraction(**overrides) -> EmailExtraction:
    data = {
        "email_type": "deal_submission",
        "summary": "Refi request",
        "deal_match": {"matched_deal_id": None, "confidence": "low", "reasoning": "new"},
        "deal_fields": [],
        "quotes": [],
    } | overrides
    return EmailExtraction.model_validate(data)


def field(name: str, value: str, source: str) -> dict:
    return {"field": name, "value": value, "source_text": source}


# ---------------------------------------------------------------- registry / schema


def test_registry_fields_have_model_columns() -> None:
    assert {f.name for f in DEAL_FIELDS} <= set(Deal.__table__.columns.keys())
    assert {f.name for f in QUOTE_FIELDS} <= set(Quote.__table__.columns.keys())


def test_schema_rejects_unknown_field_names() -> None:
    with pytest.raises(ValidationError):
        extraction(deal_fields=[field("favorite_color", "blue", "blue")])


# ---------------------------------------------------------------- coercion


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12500000", Decimal("12500000")),
        ("$12,500,000", Decimal("12500000")),
        ("12.5MM", Decimal("12500000")),
        ("850k", Decimal("850000")),
        ("1.2B", Decimal("1200000000")),
    ],
)
def test_money_coercion(raw: str, expected: Decimal) -> None:
    assert coerce_value(DEAL_FIELD_SPECS["loan_amount_requested"], raw) == expected


def test_percent_bps_ratio_integer_date_enum() -> None:
    assert coerce_value(QUOTE_FIELD_SPECS["ltv"], "65%") == Decimal("65")
    assert coerce_value(QUOTE_FIELD_SPECS["spread_bps"], "275 bps") == Decimal("275")
    assert coerce_value(QUOTE_FIELD_SPECS["min_dscr"], "1.25x") == Decimal("1.25")
    assert coerce_value(QUOTE_FIELD_SPECS["term_months"], "36") == 36
    assert coerce_value(QUOTE_FIELD_SPECS["expiration_date"], "2026-10-15") == date(2026, 10, 15)
    assert coerce_value(QUOTE_FIELD_SPECS["recourse"], "Non-Recourse") == "non_recourse"
    assert coerce_value(DEAL_FIELD_SPECS["property_type"], "Self Storage") == "self_storage"


@pytest.mark.parametrize(
    ("spec", "raw"),
    [
        (QUOTE_FIELD_SPECS["ltv"], "165"),  # above max
        (QUOTE_FIELD_SPECS["interest_rate"], "-1"),  # below min
        (QUOTE_FIELD_SPECS["term_months"], "36.5"),  # not whole
        (QUOTE_FIELD_SPECS["expiration_date"], "next Friday"),  # not ISO
        (DEAL_FIELD_SPECS["property_type"], "castle"),  # not in enum
        (DEAL_FIELD_SPECS["loan_amount_requested"], "about twelve million"),
        (DEAL_FIELD_SPECS["city"], "   "),
    ],
)
def test_invalid_values_rejected(spec, raw: str) -> None:
    with pytest.raises(CoercionError):
        coerce_value(spec, raw)


# ---------------------------------------------------------------- source verification


def test_source_matching_ignores_case_whitespace_and_quote_markers() -> None:
    norm = normalize_for_match(EMAIL)
    assert source_in_email("seeking a $12.5MM refinance", norm)
    assert source_in_email("240 units at 123 Main Street", norm)  # spans a line break
    assert source_in_email("We can offer 65% LTV at S+275", norm)  # inside "> " quoted reply
    assert source_in_email("OCCUPANCY IS 94.5%", norm)
    assert not source_in_email("seeking a $15MM refinance", norm)
    assert not source_in_email("", norm)


def test_smart_quotes_normalized() -> None:
    assert source_in_email("We’re seeking", normalize_for_match(EMAIL))


# ---------------------------------------------------------------- full validation


def test_validate_keeps_good_fields_and_reports_bad_ones() -> None:
    result = validate_extraction(
        extraction(
            deal_fields=[
                field("loan_amount_requested", "12500000", "$12.5MM refinance"),
                field("units", "240", "240 units"),
                field("occupancy_pct", "94.5", "Occupancy is 94.5%"),
                field("city", "Dallas", "Dallas, TX"),  # hallucinated source
                field("units", "250", "240 units"),  # duplicate
                field("year_built", "1650", "Parkside Apartments"),  # out of range
            ]
        ),
        EMAIL,
        candidate_deal_ids=set(),
    )
    accepted = {f.field: f.value for f in result.deal_fields}
    assert accepted == {
        "loan_amount_requested": Decimal("12500000"),
        "units": 240,
        "occupancy_pct": Decimal("94.5"),
    }
    issues = {i.field: i.reason for i in result.issues}
    assert issues["city"] == "source_text not found in email"
    assert issues["units"].startswith("duplicate field")
    assert "below minimum" in issues["year_built"]


def test_quote_without_verifiable_lender_is_dropped() -> None:
    q = {"lender_contact_name": None, "lender_contact_email": None, "option_label": None, "fields": []}
    result = validate_extraction(
        extraction(
            quotes=[
                q
                | {
                    "lender_name": {"value": "Wells Fargo", "source_text": "Wells Fargo wrote"},
                    "fields": [
                        field("ltv", "65", "65% LTV"),
                        field("spread_bps", "275", "S+275"),
                        field("term_months", "36", "3 yr term"),
                    ],
                },
                q | {"lender_name": {"value": "Chase", "source_text": "Chase quoted"}},
            ]
        ),
        EMAIL,
        candidate_deal_ids=set(),
    )
    assert [q.lender_name for q in result.quotes] == ["Wells Fargo"]
    assert {f.field for f in result.quotes[0].fields} == {"ltv", "spread_bps", "term_months"}
    assert any(i.scope == "quote[1]" and "dropped" in i.reason for i in result.issues)


def test_invalid_contact_email_is_rejected() -> None:
    result = validate_extraction(
        extraction(
            quotes=[
                {
                    "lender_name": {"value": "Wells Fargo", "source_text": "Wells Fargo"},
                    "lender_contact_name": None,
                    "lender_contact_email": {"value": "Jane Broker", "source_text": "Jane Broker"},
                    "option_label": None,
                    "fields": [],
                }
            ]
        ),
        EMAIL,
        candidate_deal_ids=set(),
    )
    assert result.quotes[0].contact_email is None
    assert any(i.field == "lender_contact_email" for i in result.issues)


def test_unknown_matched_deal_id_is_discarded() -> None:
    result = validate_extraction(
        extraction(deal_match={"matched_deal_id": "not-a-candidate", "confidence": "high", "reasoning": "x"}),
        EMAIL,
        candidate_deal_ids={"real-id"},
    )
    assert result.deal_match.matched_deal_id is None
    assert any(i.scope == "deal_match" for i in result.issues)
