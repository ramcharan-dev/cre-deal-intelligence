import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ApiError, error_responses
from app.api.schemas import DealDetail, DealEmail, DealListItem, QuoteDetail, SourcedValue
from app.db.session import get_db
from app.extraction.fields import DEAL_FIELDS, QUOTE_FIELDS, FieldSpec
from app.models import Deal, Email, ExtractedValue, Quote
from app.services.email_ingestion import canonical

router = APIRouter(prefix="/deals", tags=["deals"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "",
    response_model=list[DealListItem],
    summary="List deals",
    description="All deals, most recently updated first, with quote and email counts.",
)
async def list_deals(db: DbDep) -> list[DealListItem]:
    quote_count = select(func.count()).where(Quote.deal_id == Deal.id).correlate(Deal).scalar_subquery()
    email_count = select(func.count()).where(Email.deal_id == Deal.id).correlate(Deal).scalar_subquery()
    rows = (await db.execute(select(Deal, quote_count, email_count).order_by(Deal.updated_at.desc()))).all()
    return [
        DealListItem(
            id=d.id,
            deal_name=d.deal_name,
            property_type=d.property_type,
            city=d.city,
            state=d.state,
            loan_amount_requested=canonical(d.loan_amount_requested)
            if d.loan_amount_requested is not None
            else None,
            quote_count=qc,
            email_count=ec,
            updated_at=d.updated_at,
        )
        for d, qc, ec in rows
    ]


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
            )
        )
    return out


@router.get(
    "/{deal_id}",
    response_model=DealDetail,
    summary="Get a deal with quotes and source references",
    description=(
        "Current value of every populated deal and quote field, each with the email and verbatim "
        "`source_text` it came from, plus all emails linked to the deal."
    ),
    responses=error_responses(404),
)
async def get_deal(deal_id: uuid.UUID, db: DbDep) -> DealDetail:
    deal = (
        await db.execute(
            select(Deal)
            .where(Deal.id == deal_id)
            .options(selectinload(Deal.quotes).selectinload(Quote.lender), selectinload(Deal.emails))
        )
    ).scalar_one_or_none()
    if deal is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Deal not found")

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

    return DealDetail(
        id=deal.id,
        deal_name=deal.deal_name,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
        fields=_sourced_values(deal, "deal", DEAL_FIELDS, provenance),
        quotes=[
            QuoteDetail(
                id=q.id,
                lender_id=q.lender_id,
                lender_name=q.lender.name,
                lender_contact_name=q.lender.contact_name,
                lender_contact_email=q.lender.contact_email,
                option_label=q.option_label or None,
                fields=_sourced_values(q, "quote", QUOTE_FIELDS, provenance),
                updated_at=q.updated_at,
            )
            for q in sorted(deal.quotes, key=lambda q: (q.lender.name, q.option_label))
        ],
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
