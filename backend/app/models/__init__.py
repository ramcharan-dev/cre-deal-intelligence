"""ORM models. Deal/Quote column names match the field names in `app.extraction.fields`."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

Money = Numeric(16, 2)
Pct = Numeric(8, 4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[str | None] = mapped_column(Text, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    sender_email: Mapped[str | None] = mapped_column(Text)
    sender_name: Mapped[str | None] = mapped_column(Text)
    recipients: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"to": [...], "cc": [...]}
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    body_text: Mapped[str] = mapped_column(Text)
    raw_source: Mapped[str] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references: Mapped[list[str]] = mapped_column(JSONB, default=list)
    filename: Mapped[str | None] = mapped_column(Text)

    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="received")  # received | processed | failed
    error: Mapped[str | None] = mapped_column(Text)
    email_type: Mapped[str | None] = mapped_column(String(40))
    summary: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    raw_extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # Claude output, unvalidated
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # response snapshot returned to the UI
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    deal: Mapped["Deal | None"] = relationship(back_populates="emails")


class Deal(TimestampMixin, Base):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deal_name: Mapped[str] = mapped_column(Text)
    property_name: Mapped[str | None] = mapped_column(Text)
    property_address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(String(40))
    transaction_type: Mapped[str | None] = mapped_column(String(40))
    sponsor_name: Mapped[str | None] = mapped_column(Text)
    broker_name: Mapped[str | None] = mapped_column(Text)
    purchase_price: Mapped[Decimal | None] = mapped_column(Money)
    property_value: Mapped[Decimal | None] = mapped_column(Money)
    loan_amount_requested: Mapped[Decimal | None] = mapped_column(Money)
    noi: Mapped[Decimal | None] = mapped_column(Money)
    cap_rate: Mapped[Decimal | None] = mapped_column(Pct)
    units: Mapped[int | None] = mapped_column(Integer)
    square_feet: Mapped[int | None] = mapped_column(Integer)
    year_built: Mapped[int | None] = mapped_column(Integer)
    occupancy_pct: Mapped[Decimal | None] = mapped_column(Pct)
    target_ltv: Mapped[Decimal | None] = mapped_column(Pct)
    target_closing_date: Mapped[date | None] = mapped_column(Date)

    emails: Mapped[list[Email]] = relationship(back_populates="deal")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="deal", cascade="all, delete-orphan")


class Lender(TimestampMixin, Base):
    __tablename__ = "lenders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True)
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)

    quotes: Mapped[list["Quote"]] = relationship(back_populates="lender")


class Quote(TimestampMixin, Base):
    __tablename__ = "quotes"
    __table_args__ = (UniqueConstraint("deal_id", "lender_id", "option_label"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    lender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lenders.id", ondelete="RESTRICT"), index=True)
    option_label: Mapped[str] = mapped_column(Text, default="")  # "" when the lender gave a single option
    source_email_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("emails.id", ondelete="SET NULL"))

    loan_amount: Mapped[Decimal | None] = mapped_column(Money)
    ltv: Mapped[Decimal | None] = mapped_column(Pct)
    ltc: Mapped[Decimal | None] = mapped_column(Pct)
    rate_type: Mapped[str | None] = mapped_column(String(20))
    interest_rate: Mapped[Decimal | None] = mapped_column(Pct)
    index_name: Mapped[str | None] = mapped_column(Text)
    spread_bps: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    rate_floor: Mapped[Decimal | None] = mapped_column(Pct)
    term_months: Mapped[int | None] = mapped_column(Integer)
    amortization_months: Mapped[int | None] = mapped_column(Integer)
    interest_only_months: Mapped[int | None] = mapped_column(Integer)
    min_dscr: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    debt_yield: Mapped[Decimal | None] = mapped_column(Pct)
    origination_fee_pct: Mapped[Decimal | None] = mapped_column(Pct)
    exit_fee_pct: Mapped[Decimal | None] = mapped_column(Pct)
    prepayment_terms: Mapped[str | None] = mapped_column(Text)
    recourse: Mapped[str | None] = mapped_column(String(40))
    extension_options: Mapped[str | None] = mapped_column(Text)
    quote_status: Mapped[str | None] = mapped_column(String(40))
    expiration_date: Mapped[date | None] = mapped_column(Date)

    deal: Mapped[Deal] = relationship(back_populates="quotes")
    lender: Mapped[Lender] = relationship(back_populates="quotes")


class ExtractedValue(Base):
    """Provenance: one row per value extracted from an email, never overwritten."""

    __tablename__ = "extracted_values"
    __table_args__ = (Index("ix_extracted_values_entity", "entity_type", "entity_id", "field_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))  # deal | quote | lender
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    field_name: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)  # canonical string of the typed value
    raw_value: Mapped[str] = mapped_column(Text)  # as returned by Claude
    source_text: Mapped[str] = mapped_column(Text)  # verbatim span from the email
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_email: Mapped[Email] = relationship()


class GmailAccount(TimestampMixin, Base):
    """The connected Gmail mailbox. Only the encrypted refresh token is stored; access tokens never are."""

    __tablename__ = "gmail_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email_address: Mapped[str] = mapped_column(Text, unique=True)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text)  # Fernet ciphertext
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected | reauth_required
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_scanned: Mapped[int] = mapped_column(Integer, default=0)
    last_sync_new: Mapped[int] = mapped_column(Integer, default=0)

    messages: Mapped[list["GmailMessage"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )


class GmailMessage(Base):
    """One scanned Gmail message. `(account_id, gmail_id)` is unique, so a message is only ever fetched once.

    Relevant messages are normalized into `emails` (status `received`) and linked via `email_id`; their
    processing status is the linked email's status. Irrelevant messages keep metadata only (`email_id` null).
    """

    __tablename__ = "gmail_messages"
    __table_args__ = (UniqueConstraint("account_id", "gmail_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gmail_accounts.id", ondelete="CASCADE"), index=True
    )
    gmail_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[str | None] = mapped_column(String(64))
    email_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emails.id", ondelete="SET NULL"), index=True
    )
    subject: Mapped[str] = mapped_column(Text, default="")
    sender_name: Mapped[str | None] = mapped_column(Text)
    sender_email: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snippet: Mapped[str] = mapped_column(Text, default="")
    label_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    attachment_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    category: Mapped[str] = mapped_column(String(20))
    relevance: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped[GmailAccount] = relationship(back_populates="messages")
    email: Mapped[Email | None] = relationship()
