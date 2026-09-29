"""Registry of extractable CRE deal and lender-quote fields.

This is the single place to change what gets extracted. Each spec drives:
  * the field glossary in the Claude prompt,
  * the allowed field names in the structured-output schema,
  * type coercion / bounds checks in `validation.py`,
  * the typed column the value lands in (column name == field name on `Deal` / `Quote`).
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class FieldType(StrEnum):
    TEXT = "text"
    MONEY = "money"  # USD, whole dollars
    PERCENT = "percent"  # 6.25 means 6.25%
    BPS = "bps"  # basis points
    RATIO = "ratio"  # e.g. DSCR 1.25
    INTEGER = "integer"
    DATE = "date"  # ISO 8601 YYYY-MM-DD
    ENUM = "enum"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    type: FieldType
    description: str
    choices: tuple[str, ...] = ()
    min: Decimal | None = None
    max: Decimal | None = None


def _d(v: str) -> Decimal:
    return Decimal(v)


PROPERTY_TYPES = (
    "multifamily",
    "office",
    "retail",
    "industrial",
    "hospitality",
    "self_storage",
    "mixed_use",
    "land",
    "senior_housing",
    "student_housing",
    "manufactured_housing",
    "healthcare",
    "other",
)
TRANSACTION_TYPES = ("acquisition", "refinance", "construction", "bridge", "recapitalization", "other")

DEAL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "deal_name", "Deal name", FieldType.TEXT, "Short name for the deal as referred to in the email"
    ),
    FieldSpec("property_name", "Property name", FieldType.TEXT, "Name of the property, if it has one"),
    FieldSpec("property_address", "Property address", FieldType.TEXT, "Street address of the property"),
    FieldSpec("city", "City", FieldType.TEXT, "City of the property"),
    FieldSpec("state", "State", FieldType.TEXT, "US state, two-letter code if possible"),
    FieldSpec("property_type", "Property type", FieldType.ENUM, "Asset class", choices=PROPERTY_TYPES),
    FieldSpec(
        "transaction_type",
        "Transaction type",
        FieldType.ENUM,
        "Purpose of financing",
        choices=TRANSACTION_TYPES,
    ),
    FieldSpec("sponsor_name", "Sponsor / borrower", FieldType.TEXT, "Sponsor or borrowing entity"),
    FieldSpec("broker_name", "Broker", FieldType.TEXT, "Mortgage broker / advisor running the process"),
    FieldSpec("purchase_price", "Purchase price", FieldType.MONEY, "Purchase price", min=_d("0")),
    FieldSpec("property_value", "Property value", FieldType.MONEY, "As-is or appraised value", min=_d("0")),
    FieldSpec(
        "loan_amount_requested", "Loan amount requested", FieldType.MONEY, "Requested loan size", min=_d("0")
    ),
    FieldSpec("noi", "NOI", FieldType.MONEY, "Net operating income (annual, in-place unless stated)"),
    FieldSpec("cap_rate", "Cap rate", FieldType.PERCENT, "Capitalization rate", min=_d("0"), max=_d("25")),
    FieldSpec(
        "units", "Units", FieldType.INTEGER, "Number of units/keys/beds", min=_d("1"), max=_d("100000")
    ),
    FieldSpec("square_feet", "Square feet", FieldType.INTEGER, "Rentable square feet", min=_d("1")),
    FieldSpec("year_built", "Year built", FieldType.INTEGER, "Year built", min=_d("1700"), max=_d("2100")),
    FieldSpec(
        "occupancy_pct", "Occupancy", FieldType.PERCENT, "Physical occupancy", min=_d("0"), max=_d("100")
    ),
    FieldSpec(
        "target_ltv", "Target LTV", FieldType.PERCENT, "Requested loan-to-value", min=_d("0"), max=_d("100")
    ),
    FieldSpec("target_closing_date", "Target closing", FieldType.DATE, "Targeted closing date"),
)

QUOTE_STATUSES = ("indicative", "term_sheet", "application", "declined", "withdrawn")

QUOTE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("loan_amount", "Loan amount", FieldType.MONEY, "Quoted loan amount", min=_d("0")),
    FieldSpec("ltv", "LTV", FieldType.PERCENT, "Max loan-to-value", min=_d("0"), max=_d("100")),
    FieldSpec("ltc", "LTC", FieldType.PERCENT, "Max loan-to-cost", min=_d("0"), max=_d("100")),
    FieldSpec("rate_type", "Rate type", FieldType.ENUM, "Fixed or floating", choices=("fixed", "floating")),
    FieldSpec(
        "interest_rate",
        "Interest rate",
        FieldType.PERCENT,
        "All-in coupon (fixed) or quoted all-in rate",
        min=_d("0"),
        max=_d("30"),
    ),
    FieldSpec("index_name", "Index", FieldType.TEXT, "Benchmark index, e.g. 1M Term SOFR, 5Y UST"),
    FieldSpec("spread_bps", "Spread", FieldType.BPS, "Spread over index in bps", min=_d("0"), max=_d("2000")),
    FieldSpec(
        "rate_floor", "Rate floor", FieldType.PERCENT, "Index or rate floor", min=_d("0"), max=_d("30")
    ),
    FieldSpec(
        "term_months", "Term (months)", FieldType.INTEGER, "Initial loan term", min=_d("1"), max=_d("480")
    ),
    FieldSpec(
        "amortization_months",
        "Amortization (months)",
        FieldType.INTEGER,
        "Amortization schedule; 0 for full-term interest-only",
        min=_d("0"),
        max=_d("600"),
    ),
    FieldSpec(
        "interest_only_months",
        "Interest-only (months)",
        FieldType.INTEGER,
        "Interest-only period",
        min=_d("0"),
        max=_d("480"),
    ),
    FieldSpec(
        "min_dscr", "Min DSCR", FieldType.RATIO, "Minimum debt service coverage", min=_d("0"), max=_d("10")
    ),
    FieldSpec(
        "debt_yield", "Min debt yield", FieldType.PERCENT, "Minimum debt yield", min=_d("0"), max=_d("50")
    ),
    FieldSpec(
        "origination_fee_pct",
        "Origination fee",
        FieldType.PERCENT,
        "Origination fee",
        min=_d("0"),
        max=_d("10"),
    ),
    FieldSpec("exit_fee_pct", "Exit fee", FieldType.PERCENT, "Exit fee", min=_d("0"), max=_d("10")),
    FieldSpec(
        "prepayment_terms", "Prepayment", FieldType.TEXT, "Prepayment / yield maintenance / defeasance"
    ),
    FieldSpec(
        "recourse",
        "Recourse",
        FieldType.ENUM,
        "Recourse structure",
        choices=("non_recourse", "partial_recourse", "full_recourse"),
    ),
    FieldSpec("extension_options", "Extensions", FieldType.TEXT, "Extension options and conditions"),
    FieldSpec("quote_status", "Quote status", FieldType.ENUM, "Stage of the quote", choices=QUOTE_STATUSES),
    FieldSpec("expiration_date", "Quote expiration", FieldType.DATE, "Date the quote expires"),
)

DEAL_FIELD_SPECS: dict[str, FieldSpec] = {f.name: f for f in DEAL_FIELDS}
QUOTE_FIELD_SPECS: dict[str, FieldSpec] = {f.name: f for f in QUOTE_FIELDS}


def glossary(specs: tuple[FieldSpec, ...]) -> str:
    """Human-readable field list for the prompt."""
    lines = []
    for f in specs:
        line = f"- {f.name} ({f.type.value}): {f.description}"
        if f.choices:
            line += f". One of: {', '.join(f.choices)}"
        lines.append(line)
    return "\n".join(lines)
