"""POC Copilot: answers deal questions from stored, source-validated data.

1. A small set of common questions (compare quotes, lowest fixed rate, highest proceeds, declined lenders,
   lenders on a deal, pending actions, summary) is answered deterministically from the database.
2. Anything else falls back to keyword search and returns the matching passages with their sources.
   If a text generator is configured (`services.llm`), it could synthesize an answer from those passages;
   none is configured in the POC.

Every answer item carries the source email(s) it is based on. Nothing is inferred.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import AnswerItem, CopilotAnswer, DealDetail, SourceRef
from app.extraction.matching import normalize_name
from app.models import Deal, Email
from app.services.copilot_providers import (
    CopilotContext,
    FallbackCopilotRouter,
    create_copilot_router,
    format_search_context,
)
from app.services.deal_views import (
    HISTORICAL_AFTER,
    active,
    build_summary,
    fmt,
    load_deal_detail,
    lowest_fixed_rate,
    lowest_floating_spread,
    max_proceeds,
    quote_views,
    source_ref,
)
from app.services.search import QUOTED_REPLY, search

# ---------------------------------------------------------------- intent + deal detection

INTENTS: list[tuple[str, re.Pattern]] = [
    ("declined", re.compile(r"\b(declin\w*|pass(ed)?|turned down|withdr\w*)\b")),
    ("pending_actions", re.compile(r"\b(pending|actions?|next steps?|to-?dos?|follow[- ]?ups?|outstanding|deadlines?)\b")),
    ("compare", re.compile(r"\b(compar\w*|versus|vs\.?|side by side)\b")),
    ("lowest_fixed_rate", re.compile(r"\b(lowest|best|cheapest|lower)\b.*\b(fixed|rates?|coupon|pricing)\b")),
    ("max_proceeds", re.compile(r"\b(highest|most|largest|max\w*|biggest)\b.*\b(proceeds|loan|leverage|amount)\b")),
    ("lenders", re.compile(r"\blenders?\b")),
    ("summary", re.compile(r"\b(summar\w*|overview|status|tell me about)\b")),
]  # fmt: skip

_GENERIC_NAME_TOKENS = {
    "the", "at", "of", "and", "apartments", "apartment", "center", "building", "office", "medical", "commons",
    "lofts", "plaza", "tower", "logistics", "park",
}  # fmt: skip


def detect_intent(question: str) -> str:
    q = question.lower()
    return next((name for name, pattern in INTENTS if pattern.search(q)), "search")


def _name_tokens(d: Deal) -> set[str]:
    text = " ".join(x for x in (d.deal_name, d.property_name, d.city) if x)
    return {t for t in normalize_name(text).split() if len(t) >= 4 and t not in _GENERIC_NAME_TOKENS}


def resolve_deal(question: str, deals: list[Deal]) -> Deal | None:
    """The deal named in the question (by a distinctive name or city token), if exactly one matches best."""
    words = set(normalize_name(question).split())
    scored = sorted(((len(_name_tokens(d) & words), d) for d in deals), key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


# ---------------------------------------------------------------- handlers (one deal each)

Result = tuple[str, list[AnswerItem]]


async def _compare(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    views = quote_views(detail)
    if not views:
        return f"No lender quotes on {detail.deal_name} yet.", []
    items = [
        AnswerItem(title=v.lender, text=v.describe(), deal_id=detail.id, sources=v.key_sources())
        for v in views
    ]
    notes = []
    if best := lowest_fixed_rate(views):
        notes.append(f"lowest fixed rate {fmt(best.get('interest_rate'))} ({best.lender})")
    if best := lowest_floating_spread(views):
        notes.append(f"lowest floating spread {fmt(best.get('spread_bps'))} ({best.lender})")
    if best := max_proceeds(views):
        notes.append(f"highest proceeds {fmt(best.get('loan_amount'))} ({best.lender})")
    if declined := [v.lender for v in views if v.declined]:
        notes.append(f"declined: {', '.join(declined)}")
    return f"{detail.deal_name}: {len(views)} lender quote(s); " + "; ".join(notes) + ".", items


async def _lowest_fixed(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    views = quote_views(detail)
    best = lowest_fixed_rate(views)
    fixed = sorted(
        (v for v in active(views) if v.rate_type == "fixed" and v.num("interest_rate") is not None),
        key=lambda v: v.num("interest_rate"),
    )
    items = [
        AnswerItem(
            title=v.lender,
            text=f"{fmt(v.get('interest_rate'))} fixed"
            + (f", {fmt(v.get('loan_amount'))}" if v.get("loan_amount") else ""),
            deal_id=detail.id,
            sources=source_ref(v.get("interest_rate")) + source_ref(v.get("loan_amount")),
        )
        for v in fixed
    ]
    if floating := lowest_floating_spread(views):
        items.append(
            AnswerItem(
                title=f"{floating.lender} (floating, not directly comparable)",
                text=floating.pricing(),
                deal_id=detail.id,
                sources=source_ref(floating.get("spread_bps")),
            )
        )
    if best is None:
        return f"No fixed-rate quote with a stated rate on {detail.deal_name}.", items
    return (
        f"The lowest fixed rate on {detail.deal_name} is {fmt(best.get('interest_rate'))} from {best.lender}.",
        items,
    )


async def _max_proceeds(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    views = sorted(
        (v for v in active(quote_views(detail)) if v.num("loan_amount") is not None),
        key=lambda v: v.num("loan_amount"),
        reverse=True,
    )
    items = [
        AnswerItem(
            title=v.lender,
            text=fmt(v.get("loan_amount")),
            deal_id=detail.id,
            sources=source_ref(v.get("loan_amount")),
        )
        for v in views
    ]
    if not views:
        return f"No quoted loan amounts on {detail.deal_name}.", items
    best = views[0]
    return (
        f"The highest proceeds on {detail.deal_name} are {fmt(best.get('loan_amount'))} from {best.lender}.",
        items,
    )


async def _declined(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    declined = [v for v in quote_views(detail) if v.declined]
    items = [
        AnswerItem(
            title=v.lender,
            text=f"Status: {v.status}",
            deal_id=detail.id,
            sources=source_ref(v.get("quote_status")),
        )
        for v in declined
    ]
    if not declined:
        return f"No lender has declined {detail.deal_name}.", items
    return f"{', '.join(v.lender for v in declined)} declined {detail.deal_name}.", items


async def _lenders(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    views = quote_views(detail)
    items = []
    for v in views:
        contact = " ".join(
            x
            for x in (
                v.quote.lender_contact_name,
                f"<{v.quote.lender_contact_email}>" if v.quote.lender_contact_email else None,
            )
            if x
        )
        text = (v.status or "quote").replace("_", " ") + (f" · contact {contact}" if contact else "")
        items.append(AnswerItem(title=v.lender, text=text, deal_id=detail.id, sources=v.key_sources()))
    if not views:
        return f"No lenders have quoted {detail.deal_name} yet.", items
    return f"{len(views)} lender(s) on {detail.deal_name}: {', '.join(v.lender for v in views)}.", items


async def _summary(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    summary = build_summary(detail)
    items = [AnswerItem(text=p.text, deal_id=detail.id, sources=p.sources) for p in summary.points]
    return summary.headline, items


ACTION_RE = re.compile(
    r"\b(please|need|needs|would need|will need|let's|let me know|follow up|deadline|expected next week|"
    r"by (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2})\b",
    re.IGNORECASE,
)


def action_sentences(body: str) -> list[str]:
    """Sentences from the newest part of an email that ask for something or state a deadline (verbatim)."""
    text = QUOTED_REPLY.sub("", body)
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if ACTION_RE.search(p) and 15 <= len(p.strip()) <= 300]


async def _pending_actions(detail: DealDetail, db: AsyncSession, today: date) -> Result:
    dated: list[tuple[date, AnswerItem]] = []
    for v in active(quote_views(detail)):
        exp = v.get("expiration_date")
        if exp and date.fromisoformat(exp.value) >= today:
            days = (date.fromisoformat(exp.value) - today).days
            dated.append(
                (
                    date.fromisoformat(exp.value),
                    AnswerItem(
                        title=f"{v.lender} quote expires {fmt(exp)} ({days} days)",
                        text=f"Expiration stated by the lender ({(v.status or 'quote').replace('_', ' ')}).",
                        deal_id=detail.id,
                        sources=source_ref(exp),
                    ),
                )
            )
    closing = next((f for f in detail.fields if f.field == "target_closing_date"), None)
    if closing and date.fromisoformat(closing.value) >= today:
        dated.append(
            (
                date.fromisoformat(closing.value),
                AnswerItem(
                    title=f"Target closing {fmt(closing)}",
                    text="Closing date stated by the broker.",
                    deal_id=detail.id,
                    sources=source_ref(closing),
                ),
            )
        )
    items = [item for _, item in sorted(dated, key=lambda x: x[0])]

    emails = (
        await db.execute(
            select(Email).where(Email.deal_id == detail.id).order_by(Email.sent_at.desc().nulls_last())
        )
    ).scalars()
    for e in emails:
        for sentence in action_sentences(e.body_text):
            items.append(
                AnswerItem(
                    title=f"From {e.sender_name or e.sender_email}",
                    text=sentence,
                    deal_id=detail.id,
                    sources=[
                        SourceRef(
                            email_id=e.id,
                            email_subject=e.subject,
                            email_sender=e.sender_email,
                            email_sent_at=e.sent_at,
                            source_text=sentence,
                        )
                    ],
                )
            )
    items = items[:8]
    if not items:
        return f"No pending actions found for {detail.deal_name}.", items
    return (
        f"{len(items)} pending item(s) for {detail.deal_name} (deadlines first, then requests from emails).",
        items,
    )


HANDLERS: dict[str, Callable[[DealDetail, AsyncSession, date], Awaitable[Result]]] = {
    "compare": _compare,
    "lowest_fixed_rate": _lowest_fixed,
    "max_proceeds": _max_proceeds,
    "declined": _declined,
    "lenders": _lenders,
    "pending_actions": _pending_actions,
    "summary": _summary,
}


# ---------------------------------------------------------------- entry point


async def _search_answer(db: AsyncSession, question: str, deal: Deal | None) -> CopilotAnswer:
    hits = await search(db, question, deal.id if deal else None, limit=8)
    items = [
        AnswerItem(
            title=h.title + (f" · {h.deal_name}" if h.deal_name and h.kind != "deal" else ""),
            text=h.snippet,
            deal_id=h.deal_id,
            sources=[h.source] if h.source else [],
        )
        for h in hits
    ]
    answer = (
        f"Found {len(hits)} matching passage(s). These are the relevant sources; no AI model is configured "
        "to write a narrative answer."
        if hits
        else "No matches. Try a lender, property, city, sponsor or term (e.g. SOFR, recourse, term sheet)."
    )
    return CopilotAnswer(
        question=question,
        intent="search",
        mode="search",
        deal_id=deal.id if deal else None,
        deal_name=deal.deal_name if deal else None,
        answer=answer,
        items=items,
    )


async def _deterministic_answer(context: CopilotContext) -> CopilotAnswer:
    db = context.db
    if db is None:
        raise ValueError("Database session required for deterministic Copilot fallback")
    question = context.question
    deal = context.deal
    intent = context.intent
    today = context.today or date.today()

    if intent == "search" or (intent == "summary" and deal is None):
        return await _search_answer(db, question, deal)

    deals = (await db.execute(select(Deal).options(selectinload(Deal.emails)))).scalars().all()
    if deal is not None:
        targets = [deal]
    else:  # no deal named: answer for the active pipeline (deals with email activity in the last 180 days)
        now = datetime.now(UTC)
        targets = [
            d for d in deals if any(e.sent_at and now - e.sent_at <= HISTORICAL_AFTER for e in d.emails)
        ]
        targets.sort(key=lambda d: max((e.sent_at for e in d.emails if e.sent_at), default=now), reverse=True)

    lines, items = [], []
    for d in targets:
        detail = await load_deal_detail(db, d.id)
        if detail is None or (deal is None and intent != "pending_actions" and not detail.quotes):
            continue
        line, deal_items = await HANDLERS[intent](detail, db, today)
        lines.append(line)
        for item in deal_items:
            if deal is None and item.title:
                item.title = f"{detail.deal_name} · {item.title}"
            items.append(item)
    if not lines:
        lines.append("No active deals with matching data. Name a deal to include historical deals.")
    return CopilotAnswer(
        question=question,
        intent=intent,
        mode="structured",
        deal_id=deal.id if deal else None,
        deal_name=deal.deal_name if deal else None,
        answer="\n".join(lines),
        items=items,
    )


async def ask(
    db: AsyncSession,
    question: str,
    deal_id: uuid.UUID | None = None,
    today: date | None = None,
    router: FallbackCopilotRouter | None = None,
) -> CopilotAnswer:
    today = today or date.today()
    intent = detect_intent(question)
    deals = (await db.execute(select(Deal).options(selectinload(Deal.emails)))).scalars().all()
    deal = next((d for d in deals if d.id == deal_id), None) if deal_id else resolve_deal(question, deals)

    # 1. Retrieve relevant application data via existing search service
    hits = await search(db, question, deal.id if deal else None, limit=8)
    context_text, items = format_search_context(hits)

    # 2. Enrich context with deal details if a specific deal is targeted
    if deal is not None:
        detail = await load_deal_detail(db, deal.id)
        if detail and detail.quotes:
            views = quote_views(detail)
            quote_lines = [f"Quote from {v.lender}: {v.describe()}" for v in views]
            if quote_lines:
                context_text += "\n\nDeal Quotes:\n" + "\n".join(quote_lines)
            for v in views:
                if not any(item.title == v.lender for item in items):
                    items.append(
                        AnswerItem(title=v.lender, text=v.describe(), deal_id=deal.id, sources=v.key_sources())
                    )

    # 3. Build CopilotContext
    context = CopilotContext(
        question=question,
        deal_id=deal.id if deal else None,
        deal_name=deal.deal_name if deal else None,
        intent=intent,
        items=items,
        context_text=context_text,
        db=db,
        today=today,
        deal=deal,
    )

    # 4. Route through FallbackCopilotRouter (Gemini -> Groq -> Deterministic)
    active_router = router or create_copilot_router(fallback_fn=_deterministic_answer)
    return await active_router.answer(context)
