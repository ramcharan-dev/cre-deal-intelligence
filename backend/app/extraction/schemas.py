"""Structured-output schema Claude fills in, plus the validated form the pipeline persists.

The Claude-facing models stay deliberately loose (strings, no numeric bounds) because structured
outputs cannot express bounds; `validation.py` coerces and checks every value afterwards.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.extraction.fields import DEAL_FIELDS, QUOTE_FIELDS

DealFieldName = Literal[tuple(f.name for f in DEAL_FIELDS)]  # type: ignore[valid-type]
QuoteFieldName = Literal[tuple(f.name for f in QUOTE_FIELDS)]  # type: ignore[valid-type]

EmailType = Literal["deal_submission", "lender_quote", "deal_update", "other"]
MatchConfidence = Literal["high", "medium", "low"]


# ---------- Claude output ----------


class DealFieldExtraction(BaseModel):
    field: DealFieldName
    value: str = Field(description="Normalized value (see field types in the instructions)")
    source_text: str = Field(description="Exact, verbatim span copied from the email that states this value")


class QuoteFieldExtraction(BaseModel):
    field: QuoteFieldName
    value: str
    source_text: str


class SourcedText(BaseModel):
    value: str
    source_text: str


class LenderQuoteExtraction(BaseModel):
    lender_name: SourcedText
    lender_contact_name: SourcedText | None
    lender_contact_email: SourcedText | None
    option_label: str | None = Field(
        description="Distinguishes multiple options from the same lender, e.g. 'Fixed 5yr'; null if only one"
    )
    fields: list[QuoteFieldExtraction]


class DealMatch(BaseModel):
    matched_deal_id: str | None = Field(
        description="id of an existing candidate deal, or null for a new deal"
    )
    confidence: MatchConfidence
    reasoning: str


class EmailExtraction(BaseModel):
    email_type: EmailType
    summary: str = Field(description="One or two sentences describing what the email communicates")
    deal_match: DealMatch
    deal_fields: list[DealFieldExtraction]
    quotes: list[LenderQuoteExtraction]


# ---------- Validated (post-processing) ----------

TypedValue = str | Decimal | int | date


class ValidatedField(BaseModel):
    field: str
    value: TypedValue
    raw_value: str
    source_text: str


class ValidatedLenderQuote(BaseModel):
    lender_name: str
    lender_name_source: str
    contact_name: ValidatedField | None = None
    contact_email: ValidatedField | None = None
    option_label: str | None = None
    fields: list[ValidatedField]


class ValidationIssue(BaseModel):
    scope: str  # "deal", "quote[0]", "deal_match", ...
    field: str | None
    reason: str
    raw_value: str | None = None


class ValidatedExtraction(BaseModel):
    email_type: EmailType
    summary: str
    deal_match: DealMatch
    deal_fields: list[ValidatedField]
    quotes: list[ValidatedLenderQuote]
    issues: list[ValidationIssue]

    @property
    def has_deal_content(self) -> bool:
        return self.email_type != "other" or bool(self.deal_fields or self.quotes)
