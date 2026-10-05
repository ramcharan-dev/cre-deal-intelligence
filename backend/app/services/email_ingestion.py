import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    DealRef,
    EmailProcessingResult,
    EmailSummary,
    ExtractedFieldOut,
    LenderRef,
    QuoteOut,
)
from app.extraction.claude import ExtractionError, Extractor
from app.extraction.fields import DEAL_FIELD_SPECS, QUOTE_FIELD_SPECS, FieldSpec
from app.extraction.matching import DealCandidate, DealResolution, normalize_name, resolve_deal
from app.extraction.schemas import EmailType, TypedValue, ValidatedExtraction, ValidatedField, ValidatedLenderQuote
from app.extraction.validation import validate_extraction
from app.models import Deal, Email, ExtractedValue, Lender, Quote
from app.services.email_parser import ParsedEmail, parse_email

log = logging.getLogger(__name__)

LENDER_FIELD_LABELS = {"name": "Lender", "contact_name": "Lender contact", "contact_email": "Lender email"}

_QUOTED_REPLY_RE = re.compile(
    r"(?:^-{2,}\s*Original Message\s*-{2,}.*|^On\s+.+?wrote:.*|^From:\s*.+?\nSent:\s*.+?\nTo:\s*.+?\nSubject:\s*.*)",
    re.MULTILINE | re.DOTALL,
)
_QUOTED_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)

_DECLINE_RE = re.compile(
    r"\b(we\s*(?:will\s*)?pass(?:ed)?|unable\s*to\s*(?:quote|offer|participate|proceed)|"
    r"pass(?:ing)?\s*(?:on|at\s*this\s*time)?|declined?|turned\s*down|"
    r"out\s*of\s*(?:multifamily|office|retail|industrial)|not\s*a\s*fit|"
    r"credit\s*(?:team\s*)?passed)\b",
    re.IGNORECASE,
)

_CLOSING_RE = re.compile(
    r"\b((?:deal|loan|transaction)\s*(?:has\s*)?closed|closing\s*(?:is\s*)?confirmed|closing\s*notice|loan\s*funded|financing\s*closed)\b",
    re.IGNORECASE,
)


def reconcile_email_type(
    email_type: EmailType,
    quotes: list[ValidatedLenderQuote],
    deal_fields: list[ValidatedField],
    body_text: str,
) -> EmailType:
    """Enforces domain rules for classification regardless of extractor output."""
    # 1. If quotes with financial terms exist, it is a lender quote
    if quotes:
        return "lender_quote"

    clean_b = _QUOTED_REPLY_RE.sub("", body_text)
    clean_body = _QUOTED_LINE_RE.sub("", clean_b).strip() or body_text

    # 2. If a lender decline is stated in the body
    if _DECLINE_RE.search(clean_body):
        return "lender_quote"

    # 3. If closing or funding confirmed
    if _CLOSING_RE.search(clean_body):
        return "deal_update"

    return email_type


class IngestionError(RuntimeError):
    """Extraction failed after the email was stored; `email_id` points at the failed record."""

    def __init__(self, error: ExtractionError, email_id: uuid.UUID) -> None:
        super().__init__(error.message)
        self.error = error
        self.email_id = email_id


def canonical(value: TypedValue) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------- lookups


async def _find_existing(db: AsyncSession, parsed: ParsedEmail) -> Email | None:
    conds = [Email.content_hash == parsed.content_hash]
    if parsed.message_id:
        conds.append(Email.message_id == parsed.message_id)
    return (await db.execute(select(Email).where(or_(*conds)).limit(1))).scalar_one_or_none()


async def _thread_deal_id(db: AsyncSession, parsed: ParsedEmail) -> str | None:
    ids = parsed.thread_message_ids()
    if not ids:
        return None
    stmt = (
        select(Email.deal_id)
        .where(Email.message_id.in_(ids), Email.deal_id.is_not(None))
        .order_by(Email.sent_at.desc().nulls_last())
        .limit(1)
    )
    deal_id = (await db.execute(stmt)).scalar_one_or_none()
    return str(deal_id) if deal_id else None


async def load_candidates(db: AsyncSession, limit: int) -> list[DealCandidate]:
    deals = (await db.execute(select(Deal).order_by(Deal.updated_at.desc()).limit(limit))).scalars().all()
    if not deals:
        return []
    ranked = (
        select(
            Email.deal_id,
            Email.subject,
            func.row_number()
            .over(partition_by=Email.deal_id, order_by=Email.sent_at.desc().nulls_last())
            .label("rn"),
        )
        .where(Email.deal_id.in_([d.id for d in deals]))
        .subquery()
    )
    subjects: dict[uuid.UUID, list[str]] = {}
    for deal_id, subject, _ in (await db.execute(select(ranked).where(ranked.c.rn <= 3))).all():
        subjects.setdefault(deal_id, []).append(subject)
    return [
        DealCandidate(
            id=str(d.id),
            deal_name=d.deal_name,
            property_name=d.property_name,
            property_address=d.property_address,
            city=d.city,
            state=d.state,
            property_type=d.property_type,
            sponsor_name=d.sponsor_name,
            recent_subjects=tuple(subjects.get(d.id, [])),
        )
        for d in deals
    ]


async def _latest_source_dates(
    db: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> dict[str, datetime]:
    """Newest source-email date per field already applied to an entity."""
    stmt = (
        select(ExtractedValue.field_name, func.max(Email.sent_at))
        .join(Email, Email.id == ExtractedValue.source_email_id)
        .where(ExtractedValue.entity_type == entity_type, ExtractedValue.entity_id == entity_id)
        .group_by(ExtractedValue.field_name)
    )
    return {name: ts for name, ts in (await db.execute(stmt)).all() if ts is not None}


# ---------------------------------------------------------------- persistence helpers


@dataclass
class _Ctx:
    db: AsyncSession
    email: Email
    deal_id: uuid.UUID | None


def _record(ctx: _Ctx, entity_type: str, entity_id: uuid.UUID, f: ValidatedField) -> None:
    ctx.db.add(
        ExtractedValue(
            source_email_id=ctx.email.id,
            deal_id=ctx.deal_id,
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=f.field,
            value=canonical(f.value),
            raw_value=f.raw_value,
            source_text=f.source_text,
        )
    )


async def _apply_fields(
    ctx: _Ctx,
    entity,
    entity_type: str,
    fields: list[ValidatedField],
    specs: dict[str, FieldSpec],
    *,
    is_new: bool,
    protected: frozenset[str] = frozenset(),
) -> list[ExtractedFieldOut]:
    """Set typed columns (newer emails win) and write one provenance row per value."""
    latest = {} if is_new else await _latest_source_dates(ctx.db, entity_type, entity.id)
    sent_at = ctx.email.sent_at
    out = []
    for f in fields:
        newer_exists = sent_at is not None and f.field in latest and latest[f.field] > sent_at
        applied = is_new or (f.field not in protected and not newer_exists)
        if applied:
            setattr(entity, f.field, f.value)
        _record(ctx, entity_type, entity.id, f)
        out.append(
            ExtractedFieldOut(
                field=f.field,
                label=specs[f.field].label,
                type=specs[f.field].type.value,
                value=canonical(f.value),
                source_text=f.source_text,
                applied=applied,
            )
        )
    return out


def _new_deal_name(extraction: ValidatedExtraction, parsed: ParsedEmail) -> str:
    fields = {f.field: str(f.value) for f in extraction.deal_fields}
    for key in ("deal_name", "property_name", "property_address"):
        if fields.get(key):
            return fields[key]
    return parsed.subject or "Untitled deal"


async def _get_or_create_lender(db: AsyncSession, name: str) -> tuple[Lender, bool]:
    normalized = normalize_name(name)
    lender = (
        await db.execute(select(Lender).where(Lender.normalized_name == normalized))
    ).scalar_one_or_none()
    if lender:
        return lender, False
    lender = Lender(name=name, normalized_name=normalized)
    db.add(lender)
    await db.flush()
    return lender, True


# ---------------------------------------------------------------- main entry point


def _summary(email: Email, parsed: ParsedEmail) -> EmailSummary:
    return EmailSummary(
        id=email.id,
        subject=parsed.subject,
        sender=parsed.sender.display() if parsed.sender else None,
        to=[a.display() for a in parsed.to],
        cc=[a.display() for a in parsed.cc],
        sent_at=parsed.sent_at,
        message_id=parsed.message_id,
        filename=email.filename,
        attachment_names=parsed.attachment_names,
    )


async def ingest_email(
    db: AsyncSession,
    raw: bytes,
    filename: str | None,
    extractor: Extractor,
    max_candidates: int = 200,
) -> EmailProcessingResult:
    parsed = parse_email(raw)  # raises EmailParseError → 400
    log.info(
        "email parsed bytes=%d attachments=%d thread_refs=%d",
        len(raw),
        len(parsed.attachment_names),
        len(parsed.thread_message_ids()),
    )

    existing = await _find_existing(db, parsed)
    if existing is not None and existing.status == "processed" and existing.result:
        log.info("email %s already processed; returning stored result", existing.id)
        return EmailProcessingResult.model_validate(existing.result | {"duplicate": True})

    email = existing or Email(content_hash=parsed.content_hash)
    email.message_id = parsed.message_id
    email.subject = parsed.subject
    email.sender_email = parsed.sender.email if parsed.sender else None
    email.sender_name = parsed.sender.name if parsed.sender else None
    email.recipients = {
        "to": [{"name": a.name, "email": a.email} for a in parsed.to],
        "cc": [{"name": a.name, "email": a.email} for a in parsed.cc],
    }
    email.sent_at = parsed.sent_at
    email.body_text = parsed.body_text
    email.raw_source = raw.decode("utf-8", errors="replace")
    email.in_reply_to = parsed.in_reply_to
    email.references = parsed.references
    email.filename = filename
    email.status, email.error = "received", None
    email.model = extractor.model
    db.add(email)
    await db.commit()

    thread_deal_id = await _thread_deal_id(db, parsed)
    candidates = await load_candidates(db, max_candidates)

    try:
        raw_extraction = await extractor.extract(parsed, candidates)
    except ExtractionError as exc:
        email.status, email.error = "failed", f"[{exc.code}] {exc.message}"
        await db.commit()
        log.warning("email %s extraction failed code=%s", email.id, exc.code)
        raise IngestionError(exc, email.id) from exc
    except Exception as exc:
        log.exception("Unhandled error during email %s extraction: %s", email.id, exc)
        err = ExtractionError("upstream_error", f"Extraction failed: {type(exc).__name__}")
        email.status, email.error = "failed", f"[{err.code}] {err.message}"
        await db.commit()
        raise IngestionError(err, email.id) from exc

    email.model = getattr(extractor, "model", email.model)
    validated = validate_extraction(raw_extraction, parsed.as_prompt_text(), {c.id for c in candidates})
    resolution = resolve_deal(validated, candidates, thread_deal_id)

    try:
        result = await _persist(db, email, parsed, validated, resolution)
        email.raw_extraction = raw_extraction.model_dump(mode="json")
        email.result = result.model_dump(mode="json")
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # The exception text can echo extracted values, so only its type is stored.
        email.status, email.error = "failed", f"Saving the extraction failed ({type(exc).__name__})"
        await db.commit()
        raise
    log.info("email %s → deal %s (%s)", email.id, result.deal.id if result.deal else None, resolution.method)
    return result


async def _persist(
    db: AsyncSession,
    email: Email,
    parsed: ParsedEmail,
    validated: ValidatedExtraction,
    resolution: DealResolution,
) -> EmailProcessingResult:
    deal: Deal | None = None
    deal_created = False
    if resolution.deal_id is not None:
        deal = await db.get(Deal, uuid.UUID(resolution.deal_id))
    if deal is None and resolution.method != "none":
        deal = Deal(deal_name=_new_deal_name(validated, parsed))
        db.add(deal)
        await db.flush()
        deal_created = True

    ctx = _Ctx(db=db, email=email, deal_id=deal.id if deal else None)
    deal_fields_out: list[ExtractedFieldOut] = []
    quotes_out: list[QuoteOut] = []

    if deal is not None:
        deal_fields_out = await _apply_fields(
            ctx,
            deal,
            "deal",
            validated.deal_fields,
            DEAL_FIELD_SPECS,
            is_new=deal_created,
            protected=frozenset({"deal_name"}),
        )

        for q in validated.quotes:
            lender, lender_created = await _get_or_create_lender(db, q.lender_name)
            lender_fields = [
                ValidatedField(
                    field="name",
                    value=q.lender_name,
                    raw_value=q.lender_name,
                    source_text=q.lender_name_source,
                )
            ]
            for contact, column in ((q.contact_name, "contact_name"), (q.contact_email, "contact_email")):
                if contact is not None:
                    setattr(lender, column, contact.value)
                    lender_fields.append(contact.model_copy(update={"field": column}))
            for lf in lender_fields:
                _record(ctx, "lender", lender.id, lf)

            option = q.option_label or ""
            quote = (
                await db.execute(
                    select(Quote).where(
                        Quote.deal_id == deal.id, Quote.lender_id == lender.id, Quote.option_label == option
                    )
                )
            ).scalar_one_or_none()
            quote_created = quote is None
            if quote is None:
                quote = Quote(
                    deal_id=deal.id, lender_id=lender.id, option_label=option, source_email_id=email.id
                )
                db.add(quote)
                await db.flush()
            fields_out = await _apply_fields(
                ctx, quote, "quote", q.fields, QUOTE_FIELD_SPECS, is_new=quote_created
            )
            quotes_out.append(
                QuoteOut(
                    id=quote.id,
                    created=quote_created,
                    option_label=q.option_label,
                    lender=LenderRef(id=lender.id, name=lender.name, created=lender_created),
                    lender_contact=[
                        ExtractedFieldOut(
                            field=lf.field,
                            label=LENDER_FIELD_LABELS[lf.field],
                            type="text",
                            value=str(lf.value),
                            source_text=lf.source_text,
                            applied=True,
                        )
                        for lf in lender_fields
                        if lf.field != "name"
                    ],
                    fields=fields_out,
                )
            )
        deal.updated_at = datetime.now(UTC)

    final_email_type = reconcile_email_type(
        validated.email_type, validated.quotes, validated.deal_fields, parsed.body_text
    )
    email.deal_id = deal.id if deal else None
    email.email_type = final_email_type
    email.summary = validated.summary
    email.status = "processed"
    email.processed_at = datetime.now(UTC)

    return EmailProcessingResult(
        email=_summary(email, parsed),
        status="processed",
        email_type=final_email_type,
        summary=validated.summary,
        model=email.model,
        duplicate=False,
        deal=DealRef(
            id=deal.id,
            deal_name=deal.deal_name,
            created=deal_created,
            match_method=resolution.method,
            match_reason=resolution.reason,
        )
        if deal
        else None,
        deal_match_reasoning=validated.deal_match.reasoning,
        deal_fields=deal_fields_out,
        quotes=quotes_out,
        issues=validated.issues,
    )
