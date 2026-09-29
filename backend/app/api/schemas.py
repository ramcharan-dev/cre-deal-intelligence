"""Response models shared by the email and deal routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.extraction.matching import MatchMethod
from app.extraction.schemas import EmailType, ValidationIssue

# ---------- email processing result ----------


class EmailSummary(BaseModel):
    id: uuid.UUID
    subject: str
    sender: str | None
    to: list[str]
    cc: list[str]
    sent_at: datetime | None
    message_id: str | None
    filename: str | None
    attachment_names: list[str]


class ExtractedFieldOut(BaseModel):
    label: str
    field: str = Field(description="Field name from the extraction registry, e.g. `loan_amount`")
    type: str = Field(description="text | money (USD) | percent | bps | ratio | integer | date | enum")
    value: str = Field(description="Canonical value: plain number for numeric types, ISO date, or text")
    source_text: str = Field(description="Verbatim span from the email that states the value")
    applied: bool = Field(description="False when a newer email had already set this field, so it was kept")


class DealRef(BaseModel):
    id: uuid.UUID
    deal_name: str
    created: bool
    match_method: MatchMethod = Field(
        description="email_thread | address | claude | property_name | new (none never appears here)"
    )
    match_reason: str = Field(description="Why this deal was chosen")


class LenderRef(BaseModel):
    id: uuid.UUID
    name: str
    created: bool


class QuoteOut(BaseModel):
    id: uuid.UUID
    created: bool
    option_label: str | None = Field(description="Distinguishes several options from one lender")
    lender: LenderRef
    lender_contact: list[ExtractedFieldOut]
    fields: list[ExtractedFieldOut]


class EmailProcessingResult(BaseModel):
    email: EmailSummary
    status: str = Field(description="`processed`")
    email_type: EmailType
    summary: str = Field(description="Claude's one- or two-sentence summary")
    model: str | None = Field(description="Claude model id used for extraction")
    duplicate: bool = Field(
        description="True when this email was already processed and the stored result is returned"
    )
    deal: DealRef | None = Field(
        description="Matched or created deal; null when the email is not about a deal"
    )
    deal_match_reasoning: str = Field(description="Claude's reasoning for its deal-match decision")
    deal_fields: list[ExtractedFieldOut] = Field(description="Validated deal fields from this email")
    quotes: list[QuoteOut] = Field(description="Lender quotes created or updated from this email")
    issues: list[ValidationIssue] = Field(description="Extracted values rejected by validation, with reasons")


class EmailListItem(BaseModel):
    id: uuid.UUID
    subject: str
    sender: str | None
    sent_at: datetime | None
    created_at: datetime
    status: str = Field(description="received | processed | failed")
    email_type: str | None
    error: str | None = Field(
        description="Failure reason, prefixed with the error code, e.g. `[auth_failed] ...`"
    )
    deal_id: uuid.UUID | None
    deal_name: str | None


# ---------- deals ----------


class SourcedValue(BaseModel):
    field: str
    label: str
    type: str
    value: str
    source_email_id: uuid.UUID | None = Field(description="Email that most recently stated the current value")
    source_email_subject: str | None
    source_text: str | None = Field(description="Verbatim span from that email")
    extracted_at: datetime | None


class DealListItem(BaseModel):
    id: uuid.UUID
    deal_name: str
    property_type: str | None
    city: str | None
    state: str | None
    loan_amount_requested: str | None
    quote_count: int
    email_count: int
    updated_at: datetime


class QuoteDetail(BaseModel):
    id: uuid.UUID
    lender_id: uuid.UUID
    lender_name: str
    lender_contact_name: str | None
    lender_contact_email: str | None
    option_label: str | None
    fields: list[SourcedValue]
    updated_at: datetime


class DealEmail(BaseModel):
    id: uuid.UUID
    subject: str
    sender: str | None
    sent_at: datetime | None
    email_type: str | None
    summary: str | None


class DealDetail(BaseModel):
    id: uuid.UUID
    deal_name: str
    created_at: datetime
    updated_at: datetime
    fields: list[SourcedValue]
    quotes: list[QuoteDetail]
    emails: list[DealEmail]
