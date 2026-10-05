"""Gmail connection, sync and processing.

Sync: list message ids → skip ids already stored (duplicate protection on the Gmail message id) → fetch the
raw RFC 822 source of new ones → parse with the existing email parser → score CRE relevance → normalize
relevant messages into `emails` (status `received`) and record every scanned message in `gmail_messages`.

Processing hands the stored raw source to the existing `ingest_email` pipeline, unchanged.
"""

import html
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, distinct, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import EmailProcessingResult
from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.extraction.claude import Extractor
from app.models import Email, GmailAccount, GmailMessage
from app.services.email_ingestion import ingest_email
from app.services.email_parser import EmailParseError, ParsedEmail, parse_email
from app.services.gmail_relevance import classify
from app.services.google_client import GMAIL_SCOPE, GmailError, GoogleClient, RawMessage, TokenGrant

log = logging.getLogger(__name__)

MessageStatus = Literal["pending", "processed", "failed", "skipped"]


# ---------------------------------------------------------------- connection


async def get_account(db: AsyncSession) -> GmailAccount | None:
    """The connected mailbox (the POC supports one at a time)."""
    stmt = select(GmailAccount).order_by(GmailAccount.connected_at.desc()).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def require_account(db: AsyncSession) -> GmailAccount:
    account = await get_account(db)
    if account is None:
        raise GmailError("gmail_not_connected", "Gmail is not connected. Connect Gmail first.")
    return account


class ScopeNotGrantedError(RuntimeError):
    pass


class NoRefreshTokenError(RuntimeError):
    pass


async def connect_account(db: AsyncSession, google: GoogleClient, grant: TokenGrant) -> GmailAccount:
    """Store the mailbox behind a fresh OAuth grant, replacing any previously connected one."""
    if GMAIL_SCOPE not in grant.scopes:
        await google.revoke(grant.access_token)
        raise ScopeNotGrantedError
    email_address = await google.get_profile_email(grant.access_token)

    account = (
        await db.execute(select(GmailAccount).where(GmailAccount.email_address == email_address))
    ).scalar_one_or_none()
    if grant.refresh_token is None and account is None:
        raise NoRefreshTokenError

    others = (
        await db.execute(select(GmailAccount).where(GmailAccount.email_address != email_address))
    ).scalars()
    for other in others:
        await _revoke_stored(google, other)
        await db.delete(other)

    if account is None:
        account = GmailAccount(email_address=email_address)
        db.add(account)
    if grant.refresh_token is not None:
        account.refresh_token_encrypted = encrypt(grant.refresh_token)
    account.scopes = grant.scopes
    account.status = "connected"
    account.connected_at = datetime.now(UTC)
    await db.commit()
    log.info("gmail account connected id=%s", account.id)
    return account


async def disconnect_account(db: AsyncSession, google: GoogleClient, account: GmailAccount) -> bool:
    """Revoke at Google (best effort) and delete the account and its message index.

    Emails already normalized into `emails`, and anything extracted from them, are kept.
    """
    revoked = await _revoke_stored(google, account)
    await db.execute(delete(GmailAccount).where(GmailAccount.id == account.id))
    await db.commit()
    log.info("gmail account disconnected id=%s revoked=%s", account.id, revoked)
    return revoked


async def _revoke_stored(google: GoogleClient, account: GmailAccount) -> bool:
    token = decrypt(account.refresh_token_encrypted)
    return await google.revoke(token) if token else False


async def access_token(db: AsyncSession, google: GoogleClient, account: GmailAccount) -> str:
    """A short-lived access token from the stored refresh token. Never persisted."""
    refresh_token = decrypt(account.refresh_token_encrypted)
    try:
        if refresh_token is None:  # encryption key changed: the stored token is unreadable
            raise GmailError(
                "gmail_reauth_required", "Stored Gmail credentials are unreadable. Reconnect Gmail."
            )
        return await google.refresh_access_token(refresh_token)
    except GmailError as exc:
        if exc.code == "gmail_reauth_required" and account.status != "reauth_required":
            account.status = "reauth_required"
            await db.commit()
        raise


# ---------------------------------------------------------------- sync


@dataclass(frozen=True)
class SyncOutcome:
    scanned: int
    new: int
    duplicates: int
    relevant_new: int


def build_query(after: date | None, before: date | None, *, today: date | None = None) -> str:
    """Gmail search query for a sync window. Both bounds are inclusive; the default is the last N days."""
    if after is None and before is None:
        after = (today or datetime.now(UTC).date()) - timedelta(days=get_settings().gmail_sync_default_days)
    parts = ["-in:chats", "-category:promotions", "-category:social"]
    if after is not None:
        parts.append(f"after:{after:%Y/%m/%d}")
    if before is not None:
        parts.append(f"before:{before + timedelta(days=1):%Y/%m/%d}")  # Gmail's before: is exclusive
    return " ".join(parts)


def _normalize_raw(message: RawMessage) -> tuple[bytes, ParsedEmail | None]:
    # Stored as text (`emails.raw_source`), so normalize to the exact bytes that will later be re-encoded
    # and re-hashed by the ingestion pipeline.
    raw = message.raw.decode("utf-8", errors="replace").encode("utf-8")
    try:
        return raw, parse_email(raw)
    except EmailParseError:
        return raw, None


async def _find_or_create_email(db: AsyncSession, parsed: ParsedEmail, raw: bytes, filename: str) -> Email:
    """Normalize into the existing `emails` model, reusing a row for the same message (e.g. a prior upload)."""

    async def find() -> Email | None:
        conds = [Email.content_hash == parsed.content_hash]
        if parsed.message_id:
            conds.append(Email.message_id == parsed.message_id)
        return (await db.execute(select(Email).where(or_(*conds)).limit(1))).scalar_one_or_none()

    if (existing := await find()) is not None:
        return existing
    email = Email(
        content_hash=parsed.content_hash,
        message_id=parsed.message_id,
        subject=parsed.subject,
        sender_email=parsed.sender.email if parsed.sender else None,
        sender_name=parsed.sender.name if parsed.sender else None,
        recipients={
            "to": [{"name": a.name, "email": a.email} for a in parsed.to],
            "cc": [{"name": a.name, "email": a.email} for a in parsed.cc],
        },
        sent_at=parsed.sent_at,
        body_text=parsed.body_text,
        raw_source=raw.decode("utf-8"),
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        filename=filename,
        status="received",
    )
    try:
        async with db.begin_nested():
            db.add(email)
    except IntegrityError:  # stored concurrently by another sync or upload
        existing = await find()
        assert existing is not None
        return existing
    return email


async def sync_mailbox(
    db: AsyncSession,
    google: GoogleClient,
    account: GmailAccount,
    *,
    after: date | None,
    before: date | None,
    max_messages: int,
) -> SyncOutcome:
    token = await access_token(db, google, account)
    ids = await google.list_message_ids(token, build_query(after, before), max_messages)

    known = set()
    if ids:
        stmt = select(GmailMessage.gmail_id).where(
            GmailMessage.account_id == account.id, GmailMessage.gmail_id.in_(ids)
        )
        known = set((await db.execute(stmt)).scalars())
    new_ids = [i for i in ids if i not in known]
    fetched = await google.get_raw_messages(token, new_ids) if new_ids else []

    threshold = get_settings().gmail_relevance_threshold
    relevant_new = 0
    for message in fetched:
        raw, parsed = _normalize_raw(message)
        category, relevance = classify(parsed.subject, parsed.body_text) if parsed else ("other", 0)
        email_id: uuid.UUID | None = None
        if parsed is not None and relevance >= threshold:
            email = await _find_or_create_email(db, parsed, raw, f"gmail-{message.id}.eml")
            email_id = email.id
            relevant_new += 1
        await db.execute(
            insert(GmailMessage)
            .values(
                id=uuid.uuid4(),
                account_id=account.id,
                gmail_id=message.id,
                thread_id=message.thread_id,
                email_id=email_id,
                subject=parsed.subject if parsed else "",
                sender_name=parsed.sender.name if parsed and parsed.sender else None,
                sender_email=parsed.sender.email if parsed and parsed.sender else None,
                sent_at=(parsed.sent_at if parsed else None) or message.internal_date,
                snippet=html.unescape(message.snippet),
                label_ids=message.label_ids,
                attachment_names=parsed.attachment_names if parsed else [],
                category=category,
                relevance=relevance,
            )
            .on_conflict_do_nothing(index_elements=["account_id", "gmail_id"])
        )

    account.last_synced_at = datetime.now(UTC)
    account.last_sync_scanned = len(ids)
    account.last_sync_new = len(fetched)
    await db.commit()
    outcome = SyncOutcome(
        scanned=len(ids), new=len(fetched), duplicates=len(known), relevant_new=relevant_new
    )
    log.info(
        "gmail sync account=%s scanned=%d new=%d duplicates=%d relevant_new=%d",
        account.id,
        outcome.scanned,
        outcome.new,
        outcome.duplicates,
        outcome.relevant_new,
    )
    return outcome


# ---------------------------------------------------------------- reads


@dataclass(frozen=True)
class GmailStats:
    scanned: int
    relevant: int
    processed: int
    deals_updated: int


async def account_stats(db: AsyncSession, account: GmailAccount) -> GmailStats:
    processed = Email.status == "processed"
    stmt = (
        select(
            func.count(GmailMessage.id),
            func.count(GmailMessage.id).filter(
                GmailMessage.relevance >= get_settings().gmail_relevance_threshold
            ),
            func.count(GmailMessage.id).filter(processed),
            func.count(distinct(Email.deal_id)).filter(processed),
        )
        .select_from(GmailMessage)
        .outerjoin(Email, Email.id == GmailMessage.email_id)
        .where(GmailMessage.account_id == account.id)
    )
    scanned, relevant, processed_count, deals = (await db.execute(stmt)).one()
    return GmailStats(scanned=scanned, relevant=relevant, processed=processed_count, deals_updated=deals)


async def list_messages(db: AsyncSession, account: GmailAccount, limit: int) -> list[GmailMessage]:
    stmt = (
        select(GmailMessage)
        .where(GmailMessage.account_id == account.id)
        .options(selectinload(GmailMessage.email).selectinload(Email.deal))
        .order_by(GmailMessage.sent_at.desc().nulls_last(), GmailMessage.created_at.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def get_message(db: AsyncSession, account: GmailAccount, message_id: uuid.UUID) -> GmailMessage | None:
    stmt = (
        select(GmailMessage)
        .where(GmailMessage.id == message_id, GmailMessage.account_id == account.id)
        .options(selectinload(GmailMessage.email).selectinload(Email.deal))
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def message_status(message: GmailMessage) -> MessageStatus:
    if message.email is None:
        return "skipped"
    return {"processed": "processed", "failed": "failed"}.get(message.email.status, "pending")  # type: ignore[return-value]


# ---------------------------------------------------------------- processing


class MessageGoneError(RuntimeError):
    """The message was deleted from Gmail before it could be fetched for processing."""


async def process_message(
    db: AsyncSession,
    google: GoogleClient,
    extractor: Extractor,
    account: GmailAccount,
    message: GmailMessage,
) -> EmailProcessingResult:
    """Run the existing email pipeline on a synced message.

    Skipped (low-relevance) messages were not stored in full, so they are fetched from Gmail first.
    Raises EmailParseError / IngestionError from the pipeline, MessageGoneError, or GmailError.
    """
    if message.email_id is None:
        token = await access_token(db, google, account)
        fetched = await google.get_raw_message(token, message.gmail_id)
        if fetched is None:
            raise MessageGoneError
        raw, parsed = _normalize_raw(fetched)
        if parsed is None:
            parse_email(raw)  # re-raise the parser's specific EmailParseError
            raise AssertionError("unreachable")
        email = await _find_or_create_email(db, parsed, raw, f"gmail-{message.gmail_id}.eml")
        message.email_id = email.id
        await db.commit()

    email = await db.get(Email, message.email_id)
    assert email is not None
    return await ingest_email(
        db, email.raw_source.encode("utf-8"), email.filename, extractor, get_settings().max_deal_candidates
    )
