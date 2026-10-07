import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import ApiError, error_responses
from app.api.schemas import DealDetail, DealListItem
from app.db.session import get_db
from app.models import Deal, Email, Quote
from app.services.deal_dashboard import build_dashboard, list_status
from app.services.deal_views import HISTORICAL_AFTER, build_summary, load_deal_detail
from app.services.email_ingestion import canonical

router = APIRouter(prefix="/deals", tags=["deals"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


def _list_item(d: Deal, now: datetime) -> DealListItem:
    live = [q for q in d.quotes if q.quote_status not in ("declined", "withdrawn")]
    fixed = [q for q in live if q.rate_type == "fixed" and q.interest_rate is not None]
    best_fixed = min(fixed, key=lambda q: q.interest_rate, default=None)
    amounts = [q.loan_amount for q in live if q.loan_amount is not None]
    sent = [e.sent_at for e in d.emails if e.sent_at is not None]
    last_activity = max(sent, default=None)
    status = list_status(d.quotes, d.emails, now)
    return DealListItem(
        id=d.id,
        deal_name=d.deal_name,
        property_type=d.property_type,
        city=d.city,
        state=d.state,
        loan_amount_requested=canonical(d.loan_amount_requested)
        if d.loan_amount_requested is not None
        else None,
        quote_count=len(d.quotes),
        email_count=len(d.emails),
        updated_at=d.updated_at,
        lender_count=len({q.lender_id for q in d.quotes}),
        max_loan_amount=canonical(max(amounts)) if amounts else None,
        lowest_fixed_rate=canonical(best_fixed.interest_rate) if best_fixed else None,
        lowest_fixed_rate_lender=best_fixed.lender.name if best_fixed else None,
        last_activity_at=last_activity,
        is_historical=last_activity is not None and now - last_activity > HISTORICAL_AFTER,
        status=status.code,
        status_label=status.label,
        sponsor_name=d.sponsor_name,
        property_name=d.property_name,
    )


@router.get(
    "",
    response_model=list[DealListItem],
    summary="List deals",
    description=(
        "All deals, most recent email activity first, with quote/email/lender counts and best terms "
        "(highest proceeds, lowest fixed rate). Deals with no email in 180 days are marked historical."
    ),
)
async def list_deals(db: DbDep) -> list[DealListItem]:
    deals = (
        await db.execute(
            select(Deal).options(
                selectinload(Deal.quotes).selectinload(Quote.lender), selectinload(Deal.emails)
            )
        )
    ).scalars()
    now = datetime.now(UTC)
    items = [_list_item(d, now) for d in deals]
    return sorted(items, key=lambda i: i.last_activity_at or i.updated_at, reverse=True)


@router.get(
    "/{deal_id}",
    response_model=DealDetail,
    summary="Get a deal dashboard: quotes, lenders, documents, activities, pending actions, summary",
    description=(
        "Current value of every populated deal and quote field, each with the email and verbatim "
        "`source_text` it came from, all emails linked to the deal, and a summary built from those values. "
        "Also the deal status, lenders approached (responded or awaiting a response), attachments, an activity "
        "log (including meetings/calls mentioned in emails) and pending actions with the responsible person "
        "and due date, each linked to its source email."
    ),
    responses=error_responses(404),
)
async def get_deal(deal_id: uuid.UUID, db: DbDep) -> DealDetail:
    detail = await load_deal_detail(db, deal_id)
    if detail is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Deal not found")
    detail.summary = build_summary(detail)
    emails = (await db.execute(select(Email).where(Email.deal_id == deal_id))).scalars().all()
    build_dashboard(detail, list(emails))
    return detail
