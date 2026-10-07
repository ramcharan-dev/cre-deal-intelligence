"""Read models for deals: current values with provenance, quote comparison helpers, deterministic summary.

Everything here is computed from stored, validated values; nothing is inferred. Every statement in a summary
carries the source email (and verbatim text) of the values it is built from.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    DealDetail,
    DealEmail,
    DealSummary,
    QuoteDetail,
    SourcedValue,
    SourceRef,
    SummaryPoint,
)
from app.extraction.fields import DEAL_FIELDS, QUOTE_FIELDS, FieldSpec
from app.models import Deal, ExtractedValue, Quote
from app.services.email_ingestion import canonical

# Deals with no email activity for this long are shown as historical.
HISTORICAL_AFTER = timedelta(days=180)

# ---------------------------------------------------------------- loading


def _sourced_values(entity, entity_type: str, specs: tuple[FieldSpec, ...], provenance) -> list[SourcedValue]:
    """Current value of each populated field, with the newest email that stated that value."""
    out = []
    for spec in specs:
        current = getattr(entity, spec.name)
        if current is None:
            continue
        value = canonical(current)
        rows = provenance.get((entity_type, entity.id, spec.name), [])
        src = next((r for r in rows if r.value == value), None)
        out.append(
            SourcedValue(
                field=spec.name,
                label=spec.label,
                type=spec.type.value,
                value=value,
                source_email_id=src.source_email_id if src else None,
                source_email_subject=src.source_email.subject if src else None,
                source_text=src.source_text if src else None,
                extracted_at=src.created_at if src else None,
                source_email_sender=src.source_email.sender_email if src else None,
                source_email_sent_at=src.source_email.sent_at if src else None,
            )
        )
    return out


async def load_deal_detail(db: AsyncSession, deal_id: uuid.UUID) -> DealDetail | None:
    deal = (
        await db.execute(
            select(Deal)
            .where(Deal.id == deal_id)
            .options(selectinload(Deal.quotes).selectinload(Quote.lender), selectinload(Deal.emails))
        )
    ).scalar_one_or_none()
    if deal is None:
        return None

    rows = (
        await db.execute(
            select(ExtractedValue)
            .where(ExtractedValue.deal_id == deal.id, ExtractedValue.entity_type.in_(("deal", "quote")))
            .options(selectinload(ExtractedValue.source_email))
            .order_by(ExtractedValue.created_at.desc())
        )
    ).scalars()
    provenance: dict[tuple, list[ExtractedValue]] = {}
    for r in rows:
        provenance.setdefault((r.entity_type, r.entity_id, r.field_name), []).append(r)

    today = date.today()

    def quote_detail(q: Quote) -> QuoteDetail:
        q_rows = [
            r for (kind, entity, _), rs in provenance.items() if (kind, entity) == ("quote", q.id) for r in rs
        ]
        q_rows.sort(key=lambda r: r.source_email.sent_at or r.created_at)
        first = q_rows[0] if q_rows else None
        fields = _sourced_values(q, "quote", QUOTE_FIELDS, provenance)
        validity, days = "no_expiry", None
        if q.expiration_date is not None:
            days = (q.expiration_date - today).days
            validity = "expired" if days < 0 else "expiring_soon" if days <= 7 else "valid"
        return QuoteDetail(
            id=q.id,
            lender_id=q.lender_id,
            lender_name=q.lender.name,
            lender_contact_name=q.lender.contact_name,
            lender_contact_email=q.lender.contact_email,
            option_label=q.option_label or None,
            fields=fields,
            updated_at=q.updated_at,
            quote_date=first.source_email.sent_at if first else None,
            last_updated_at=q_rows[-1].source_email.sent_at if q_rows else None,
            source=SourceRef(
                email_id=first.source_email_id,
                email_subject=first.source_email.subject,
                email_sender=first.source_email.sender_email,
                email_sent_at=first.source_email.sent_at,
                label="Quote",
            )
            if first
            else None,
            validity=validity,
            days_to_expiry=days,
        )

    return DealDetail(
        id=deal.id,
        deal_name=deal.deal_name,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
        fields=_sourced_values(deal, "deal", DEAL_FIELDS, provenance),
        quotes=[quote_detail(q) for q in sorted(deal.quotes, key=lambda q: (q.lender.name, q.option_label))],
        emails=[
            DealEmail(
                id=e.id,
                subject=e.subject,
                sender=e.sender_email,
                sent_at=e.sent_at,
                email_type=e.email_type,
                summary=e.summary,
            )
            for e in sorted(deal.emails, key=lambda e: e.sent_at or e.created_at, reverse=True)
        ],
    )


# ---------------------------------------------------------------- formatting / sources


def source_ref(v: SourcedValue | None) -> list[SourceRef]:
    if v is None or v.source_email_id is None:
        return []
    return [
        SourceRef(
            email_id=v.source_email_id,
            email_subject=v.source_email_subject or "",
            email_sender=v.source_email_sender,
            email_sent_at=v.source_email_sent_at,
            source_text=v.source_text,
            label=v.label,
        )
    ]


def _dec(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value is not None else None
    except InvalidOperation:
        return None


def fmt(v: SourcedValue | None) -> str:
    """Human-readable value, mirroring frontend/src/lib/format.ts."""
    if v is None:
        return "—"
    n = _dec(v.value)
    match v.type:
        case "money" if n is not None:
            return f"${n:,.0f}"
        case "percent" if n is not None:
            return f"{n:f}%"
        case "bps" if n is not None:
            return f"{n:f} bps"
        case "ratio" if n is not None:
            return f"{n:f}x"
        case "integer" if n is not None:
            return f"{n:,.0f}"
        case "enum":
            return v.value.replace("_", " ")
        case "date":
            d = date.fromisoformat(v.value)
            return f"{d:%b} {d.day}, {d.year}"
    return v.value


# ---------------------------------------------------------------- quote comparison


@dataclass
class QuoteView:
    quote: QuoteDetail

    def __post_init__(self) -> None:
        self.values = {f.field: f for f in self.quote.fields}

    @property
    def lender(self) -> str:
        q = self.quote
        return f"{q.lender_name} ({q.option_label})" if q.option_label else q.lender_name

    def get(self, field: str) -> SourcedValue | None:
        return self.values.get(field)

    def num(self, field: str) -> Decimal | None:
        return _dec(self.values[field].value) if field in self.values else None

    @property
    def status(self) -> str | None:
        v = self.values.get("quote_status")
        return v.value if v else None

    @property
    def declined(self) -> bool:
        return self.status in ("declined", "withdrawn")

    @property
    def rate_type(self) -> str | None:
        v = self.values.get("rate_type")
        return v.value if v else None

    def pricing(self) -> str:
        if self.rate_type == "floating" or (self.get("spread_bps") and not self.get("interest_rate")):
            index = self.values.get("index_name")
            spread = self.values.get("spread_bps")
            if spread:
                return f"{index.value if index else 'index'} + {fmt(spread)} (floating)"
        if self.get("interest_rate"):
            return f"{self.rate_type or 'rate'} {fmt(self.get('interest_rate'))}"
        return "no rate stated"

    def describe(self) -> str:
        if self.declined:
            return f"{self.lender}: declined"
        parts = [fmt(self.get("loan_amount")) if self.get("loan_amount") else "no loan amount stated"]
        parts.append(self.pricing())
        if self.get("term_months"):
            parts.append(f"{fmt(self.get('term_months'))}-month term")
        for field in ("ltv", "ltc"):
            if self.get(field):
                parts.append(f"{fmt(self.get(field))} {field.upper()}")
        if self.get("recourse"):
            parts.append(fmt(self.get("recourse")))
        if self.status:
            parts.append(self.status.replace("_", " "))
        return f"{self.lender}: " + ", ".join(parts)

    def key_sources(self) -> list[SourceRef]:
        fields = ("quote_status",) if self.declined else ("loan_amount", "interest_rate", "spread_bps")
        return [s for f in fields for s in source_ref(self.get(f))]


def quote_views(detail: DealDetail) -> list[QuoteView]:
    return [QuoteView(q) for q in detail.quotes]


def active(views: list[QuoteView]) -> list[QuoteView]:
    return [v for v in views if not v.declined]


def lowest_fixed_rate(views: list[QuoteView]) -> QuoteView | None:
    """Only fixed-rate quotes with a stated rate are comparable; floating quotes are compared by spread."""
    fixed = [v for v in active(views) if v.rate_type == "fixed" and v.num("interest_rate") is not None]
    return min(fixed, key=lambda v: v.num("interest_rate"), default=None)


def lowest_floating_spread(views: list[QuoteView]) -> QuoteView | None:
    floating = [v for v in active(views) if v.rate_type == "floating" and v.num("spread_bps") is not None]
    return min(floating, key=lambda v: v.num("spread_bps"), default=None)


def max_proceeds(views: list[QuoteView]) -> QuoteView | None:
    with_amount = [v for v in active(views) if v.num("loan_amount") is not None]
    return max(with_amount, key=lambda v: v.num("loan_amount"), default=None)


# ---------------------------------------------------------------- summary


def build_summary(detail: DealDetail) -> DealSummary:
    """Template summary from stored values only; each point lists the sources it was built from."""
    f = {v.field: v for v in detail.fields}
    points: list[SummaryPoint] = []

    def add(text: str, *values: SourcedValue | None, extra: list[SourceRef] | None = None) -> None:
        sources = [s for v in values for s in source_ref(v)] + (extra or [])
        points.append(SummaryPoint(text=text, sources=sources))

    size = (
        f"{fmt(f['units'])}-unit "
        if "units" in f
        else f"{fmt(f['square_feet'])} SF "
        if "square_feet" in f
        else ""
    )
    kind = fmt(f["property_type"]) if "property_type" in f else "CRE"
    where = ", ".join(fmt(f[k]) for k in ("city", "state") if k in f)
    add(
        f"{detail.deal_name} is a {size}{kind} property{f' in {where}' if where else ''}.",
        f.get("units") or f.get("square_feet"),
        f.get("property_type"),
        f.get("city"),
    )

    if "loan_amount_requested" in f or "transaction_type" in f:
        who = fmt(f["sponsor_name"]) if "sponsor_name" in f else "The sponsor"
        amount = f"{fmt(f['loan_amount_requested'])} " if "loan_amount_requested" in f else ""
        purpose = fmt(f["transaction_type"]) if "transaction_type" in f else "financing"
        phrase = f"{amount}{purpose}"
        text = f"{who} is seeking {'an' if phrase[:1].lower() in 'aeiou' else 'a'} {phrase} loan"
        if "target_ltv" in f:
            text += f" at about {fmt(f['target_ltv'])} LTV"
        if "target_closing_date" in f:
            text += f", targeting closing on {fmt(f['target_closing_date'])}"
        add(
            text + ".",
            f.get("sponsor_name"),
            f.get("loan_amount_requested"),
            f.get("transaction_type"),
            f.get("target_ltv"),
            f.get("target_closing_date"),
        )

    views = quote_views(detail)
    if views:
        declined = [v for v in views if v.declined]
        text = f"{len(views)} lender response(s): {', '.join(v.lender for v in views)}."
        if declined:
            text += f" Declined: {', '.join(v.lender for v in declined)}."
        add(text, *[v.get("quote_status") for v in declined])
        if best := lowest_fixed_rate(views):
            add(
                f"Lowest fixed rate: {fmt(best.get('interest_rate'))} from {best.lender}.",
                best.get("interest_rate"),
            )
        if best := lowest_floating_spread(views):
            add(f"Lowest floating spread: {best.pricing()} from {best.lender}.", best.get("spread_bps"))
        if best := max_proceeds(views):
            add(
                f"Highest proceeds: {fmt(best.get('loan_amount'))} from {best.lender}.",
                best.get("loan_amount"),
            )
    else:
        add("No lender quotes received yet.")

    if detail.emails:
        latest = detail.emails[0]
        if latest.summary:
            when = (
                f"{latest.sent_at:%b} {latest.sent_at.day}, {latest.sent_at.year}" if latest.sent_at else ""
            )
            points.append(
                SummaryPoint(
                    text=f"Latest email{f' ({when})' if when else ''}: {latest.summary}",
                    sources=[
                        SourceRef(
                            email_id=latest.id,
                            email_subject=latest.subject,
                            email_sender=latest.sender,
                            email_sent_at=latest.sent_at,
                        )
                    ],
                )
            )

    headline = points[1].text if len(points) > 1 and "seeking" in points[1].text else points[0].text
    return DealSummary(headline=headline, points=points)
