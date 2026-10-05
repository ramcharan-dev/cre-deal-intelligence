"""Request/response models for the email, deal and Gmail routes."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    source_email_sender: str | None = None
    source_email_sent_at: datetime | None = None


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
    lender_count: int = 0
    max_loan_amount: str | None = Field(
        None, description="Largest quoted loan amount (declined quotes excluded)"
    )
    lowest_fixed_rate: str | None = Field(None, description="Lowest fixed interest rate quoted (percent)")
    lowest_fixed_rate_lender: str | None = None
    last_activity_at: datetime | None = Field(None, description="Date of the newest email linked to the deal")
    is_historical: bool = Field(False, description="No email activity in the last 180 days")


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
    summary: "DealSummary | None" = None



# ---------- gmail ----------

GmailCategoryOut = Literal["lender_quote", "deal_update", "financing", "term_sheet", "follow_up", "other"]


class GmailLastSync(BaseModel):
    at: datetime
    scanned: int = Field(description="Message ids returned by Gmail for the sync window")
    new: int = Field(description="Messages fetched for the first time (the rest were already stored)")


class GmailConnection(BaseModel):
    configured: bool = Field(
        description="False until GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and TOKEN_ENCRYPTION_KEY are set"
    )
    connected: bool
    status: Literal["not_connected", "connected", "reauth_required"] = Field(
        description="`reauth_required` when Google rejected the stored refresh token; reconnect to fix"
    )
    email_address: str | None = Field(None, examples=["priya.raman@harborviewcap.com"])
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes granted by the user")
    connected_at: datetime | None = None
    last_sync: GmailLastSync | None = None


class GmailDisconnectResult(BaseModel):
    disconnected: bool = Field(description="False when no mailbox was connected")
    revoked: bool = Field(description="Whether Google confirmed the token revocation (best effort)")


class GmailStatsOut(BaseModel):
    scanned: int = Field(description="Messages scanned across all syncs")
    relevant: int = Field(description="Messages at or above the CRE relevance threshold")
    processed: int = Field(description="Messages processed by the email intelligence pipeline")
    deals_updated: int = Field(description="Distinct deals created or updated from processed messages")


class GmailSyncRequest(BaseModel):
    after: date | None = Field(
        None, description="First day to include (inclusive). Defaults to 30 days ago when both are omitted."
    )
    before: date | None = Field(None, description="Last day to include (inclusive)")
    max_messages: int = Field(100, ge=1, le=500, description="Newest messages to scan in the window")

    @model_validator(mode="after")
    def _check_range(self) -> "GmailSyncRequest":
        if self.after and self.before and self.after > self.before:
            raise ValueError("`after` must be on or before `before`")
        return self


class GmailSyncResult(BaseModel):
    scanned: int = Field(description="Message ids returned by Gmail for the window")
    new: int = Field(description="Messages fetched and stored for the first time")
    duplicates: int = Field(description="Messages skipped because their Gmail id was already stored")
    relevant_new: int = Field(description="New messages normalized into `emails` for processing")
    synced_at: datetime
    stats: GmailStatsOut


class GmailMessageOut(BaseModel):
    id: uuid.UUID
    gmail_id: str = Field(description="Gmail message id (unique per mailbox; used for duplicate protection)")
    thread_id: str | None
    subject: str
    sender_name: str | None
    sender_email: str | None
    sent_at: datetime | None
    snippet: str = Field(description="Gmail's plain-text preview")
    category: GmailCategoryOut = Field(description="Keyword-based CRE category assigned at sync")
    relevance: int = Field(ge=0, le=100, description="Keyword-based CRE relevance score assigned at sync")
    status: Literal["pending", "processed", "failed", "skipped"] = Field(
        description="`skipped`: below the relevance threshold (can still be processed); otherwise the status "
        "of the linked email record"
    )
    error: str | None = Field(description="Processing failure, prefixed with the error code")
    attachment_names: list[str]
    email_id: uuid.UUID | None = Field(description="Linked `emails` record (null when skipped)")
    email_type: str | None = Field(description="Set by Claude once processed")
    deal_id: uuid.UUID | None
    deal_name: str | None


class GmailMessageDetail(GmailMessageOut):
    to: list[str]
    cc: list[str]
    body_text: str | None = Field(
        description="Plain-text body; null for skipped messages, which are not stored in full"
    )
    gmail_url: str = Field(description="Opens the message in Gmail")


class GmailMessageList(BaseModel):
    messages: list[GmailMessageOut]
    stats: GmailStatsOut
    last_sync: GmailLastSync | None


class GmailProcessResult(BaseModel):
    message: GmailMessageOut = Field(description="The message with its updated status")
    result: EmailProcessingResult = Field(description="Output of the email intelligence pipeline")

    


# ---------- source references, summary, search, copilot ----------


class SourceRef(BaseModel):
    """Where a statement comes from: an email and, when available, the verbatim text."""

    email_id: uuid.UUID
    email_subject: str
    email_sender: str | None
    email_sent_at: datetime | None
    source_text: str | None = None
    label: str | None = Field(None, description="Field label, e.g. `Interest rate`")


class SummaryPoint(BaseModel):
    text: str
    sources: list[SourceRef]


class DealSummary(BaseModel):
    headline: str
    points: list[SummaryPoint]


class EmailSource(BaseModel):
    id: uuid.UUID
    subject: str
    sender: str | None
    sent_at: datetime | None
    deal_id: uuid.UUID | None
    email_type: str | None
    summary: str | None
    attachment_names: list[str]
    text: str = Field(
        description="Headers + body exactly as the extractor saw them; `source_text` spans occur here"
    )


class SearchHit(BaseModel):
    kind: str = Field(description="deal | field | email")
    title: str
    snippet: str
    deal_id: uuid.UUID | None
    deal_name: str | None
    score: float
    source: SourceRef | None


class CopilotRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    deal_id: uuid.UUID | None = Field(None, description="Limit the question to one deal")


class AnswerItem(BaseModel):
    title: str | None = None
    text: str
    deal_id: uuid.UUID | None = None
    sources: list[SourceRef] = []


class CopilotAnswer(BaseModel):
    question: str
    intent: str = Field(
        description="compare | lowest_fixed_rate | max_proceeds | declined | pending_actions | "
        "lenders | summary | search"
    )
    mode: str = Field(description="structured (computed from stored data), search (extractive), or llm")
    deal_id: uuid.UUID | None
    deal_name: str | None
    answer: str
    items: list[AnswerItem]


DealDetail.model_rebuild()

