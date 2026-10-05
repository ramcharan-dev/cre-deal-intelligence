"""Keyword search over deals, extracted values (with their verbatim source text) and email text.

Plain ILIKE matching, which is enough for a POC-sized dataset. No embeddings, no external calls.
"""

import re
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import SearchHit, SourceRef
from app.extraction.fields import DEAL_FIELD_SPECS, QUOTE_FIELD_SPECS
from app.models import Deal, Email, ExtractedValue, Quote

STOPWORDS = {
    "the", "and", "for", "with", "what", "which", "who", "whom", "are", "was", "were", "has", "have", "had",
    "does", "did", "this", "that", "these", "those", "from", "about", "any", "all", "our", "their", "there",
    "show", "find", "search", "tell", "give", "list", "get", "deal", "deals", "info", "information",
    "historical", "history", "past", "previous", "please", "can", "you", "how", "much", "many", "when",
    "where", "into", "on", "in", "of", "to", "is", "it", "me", "we", "us", "a", "an", "or",
}  # fmt: skip

QUOTED_REPLY = re.compile(r"(?ms)^-{2,}\s*Original Message\s*-{2,}.*|^>.*?$")


def query_terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9$%.\-]*[a-z0-9%]|[a-z0-9]", query.lower())
    terms = []
    for w in words:
        if len(w) >= 3 and w not in STOPWORDS and w not in terms:
            terms.append(w)
    return terms[:8]


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _score(terms: list[str], *texts: str | None) -> int:
    haystack = " ".join(t for t in texts if t).lower()
    return sum(1 for t in terms if t in haystack)


def snippet(text: str, terms: list[str], width: int = 160) -> str:
    """A window of `text` around the first matched term, without quoted reply history."""
    body = QUOTED_REPLY.sub("", text).strip() or text
    flat = re.sub(r"\s+", " ", body)
    positions = [i for t in terms if (i := flat.lower().find(t)) >= 0]
    if not positions:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, min(positions) - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def _email_ref(e: Email, source_text: str | None = None, label: str | None = None) -> SourceRef:
    return SourceRef(
        email_id=e.id,
        email_subject=e.subject,
        email_sender=e.sender_email,
        email_sent_at=e.sent_at,
        source_text=source_text,
        label=label,
    )


async def search(
    db: AsyncSession, query: str, deal_id: uuid.UUID | None = None, limit: int = 10
) -> list[SearchHit]:
    terms = query_terms(query)
    if not terms:
        return []
    deal_names = {d.id: d.deal_name for d in (await db.execute(select(Deal))).scalars()}
    hits: list[tuple[SearchHit, object]] = []  # (hit, sort date)

    # Deals by name, location and parties.
    deal_cols = (
        Deal.deal_name,
        Deal.property_name,
        Deal.city,
        Deal.state,
        Deal.sponsor_name,
        Deal.broker_name,
    )
    stmt = select(Deal).where(or_(*[c.ilike(_like(t), escape="\\") for t in terms for c in deal_cols]))
    if deal_id:
        stmt = stmt.where(Deal.id == deal_id)
    for d in (await db.execute(stmt)).scalars():
        facts = [d.property_type, ", ".join(x for x in (d.city, d.state) if x), d.sponsor_name]
        hits.append(
            (
                SearchHit(
                    kind="deal",
                    title=d.deal_name,
                    snippet=" · ".join(x.replace("_", " ") for x in facts if x),
                    deal_id=d.id,
                    deal_name=d.deal_name,
                    score=_score(terms, *(getattr(d, c.key) for c in deal_cols)) + 0.5,
                    source=None,
                ),
                d.updated_at,
            )
        )

    # Extracted values: match the verbatim source text, the field name, or the lender.
    stmt = (
        select(ExtractedValue)
        .where(
            ExtractedValue.deal_id.is_not(None),
            ExtractedValue.entity_type.in_(("deal", "quote")),
            or_(
                *[ExtractedValue.source_text.ilike(_like(t), escape="\\") for t in terms],
                *[ExtractedValue.field_name.ilike(_like(t), escape="\\") for t in terms],
            ),
        )
        .options(selectinload(ExtractedValue.source_email))
    )
    if deal_id:
        stmt = stmt.where(ExtractedValue.deal_id == deal_id)
    values = (await db.execute(stmt)).scalars().all()
    quote_ids = {v.entity_id for v in values if v.entity_type == "quote"}
    lenders = {}
    if quote_ids:
        quotes = await db.execute(
            select(Quote).where(Quote.id.in_(quote_ids)).options(selectinload(Quote.lender))
        )
        lenders = {q.id: q.lender.name for q in quotes.scalars()}
    for v in values:
        specs = QUOTE_FIELD_SPECS if v.entity_type == "quote" else DEAL_FIELD_SPECS
        label = specs[v.field_name].label if v.field_name in specs else v.field_name
        lender = lenders.get(v.entity_id)
        deal_name = deal_names.get(v.deal_id)
        title = f"{label}: {v.value.replace('_', ' ')}" + (f" — {lender}" if lender else "")
        hits.append(
            (
                SearchHit(
                    kind="field",
                    title=title,
                    snippet=v.source_text,
                    deal_id=v.deal_id,
                    deal_name=deal_name,
                    score=_score(terms, label, v.value, v.source_text, lender, deal_name) + 0.25,
                    source=_email_ref(v.source_email, v.source_text, label),
                ),
                v.source_email.sent_at,
            )
        )

    # Email subjects and bodies.
    stmt = select(Email).where(
        Email.status == "processed",
        or_(*[c.ilike(_like(t), escape="\\") for t in terms for c in (Email.subject, Email.body_text)]),
    )
    if deal_id:
        stmt = stmt.where(Email.deal_id == deal_id)
    for e in (await db.execute(stmt)).scalars():
        hits.append(
            (
                SearchHit(
                    kind="email",
                    title=e.subject or "(no subject)",
                    snippet=snippet(e.body_text, terms),
                    deal_id=e.deal_id,
                    deal_name=deal_names.get(e.deal_id) if e.deal_id else None,
                    score=_score(terms, e.subject, e.body_text),
                    source=_email_ref(e),
                ),
                e.sent_at,
            )
        )

    hits.sort(key=lambda h: (h[0].score, h[1].timestamp() if h[1] else 0), reverse=True)
    return [h for h, _ in hits[:limit]]
