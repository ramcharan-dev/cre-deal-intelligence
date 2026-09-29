import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import EXTRACTION_STATUS, ApiError, error_responses
from app.api.schemas import EmailListItem, EmailProcessingResult
from app.core.config import get_settings
from app.db.session import get_db
from app.extraction.claude import Extractor, get_extractor
from app.models import Email
from app.services.email_ingestion import IngestionError, ingest_email
from app.services.email_parser import EmailParseError

router = APIRouter(prefix="/emails", tags=["emails"])

DbDep = Annotated[AsyncSession, Depends(get_db)]


class EmailUploadForm(BaseModel):
    """multipart/form-data body for an email upload."""

    file: UploadFile = Field(
        description="Raw email as RFC 822 / .eml (headers + body). Exported from Outlook, Gmail "
        "(⋮ → Download message) or Apple Mail. Max 10 MB."
    )


@router.post(
    "",
    response_model=EmailProcessingResult,
    summary="Upload an email and extract deal data",
    description=(
        "Runs the full pipeline synchronously (typically 20–90 s):\n\n"
        "1. **Parse** subject, sender, recipients, date, thread headers and text body.\n"
        "2. **Extract** deal and lender-quote fields with Claude (structured output). "
        "Every value carries a verbatim `source_text` span.\n"
        "3. **Validate** each value's type/bounds and that its `source_text` occurs in the email; "
        "rejects are returned in `issues`.\n"
        "4. **Match** to an existing deal (thread → address → Claude → property name) or create one.\n"
        "5. **Persist** deal, lender and quote records plus a provenance row per value "
        "(`source_email_id` + `source_text`).\n\n"
        "Uploading an already processed email returns the stored result with `duplicate: true` and no "
        "Claude call. A previously failed email is re-processed."
    ),
    responses=error_responses(400, 413, 422, 429, 502, 503, 504),
)
async def upload_email(
    form: Annotated[EmailUploadForm, Form(media_type="multipart/form-data")],
    db: DbDep,
    extractor: Annotated[Extractor, Depends(get_extractor)],
) -> EmailProcessingResult:
    settings = get_settings()
    raw = await form.file.read(settings.max_email_bytes + 1)
    if len(raw) > settings.max_email_bytes:
        raise ApiError(status.HTTP_413_CONTENT_TOO_LARGE, "too_large", "Email exceeds the 10 MB upload limit")
    try:
        return await ingest_email(db, raw, form.file.filename, extractor, settings.max_deal_candidates)
    except EmailParseError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_email", str(exc)) from exc
    except IngestionError as exc:
        err = exc.error
        raise ApiError(
            EXTRACTION_STATUS[err.code],
            err.code,
            err.message,
            retryable=err.retryable,
            email_id=str(exc.email_id),
        ) from exc


@router.get(
    "",
    response_model=list[EmailListItem],
    summary="List recent emails",
    description="Most recently uploaded first, including failed uploads with their error.",
)
async def list_emails(
    db: DbDep, limit: Annotated[int, Query(ge=1, le=200, description="Maximum number of emails")] = 50
) -> list[EmailListItem]:
    emails = (
        await db.execute(
            select(Email).options(selectinload(Email.deal)).order_by(Email.created_at.desc()).limit(limit)
        )
    ).scalars()
    return [
        EmailListItem(
            id=e.id,
            subject=e.subject,
            sender=e.sender_email,
            sent_at=e.sent_at,
            created_at=e.created_at,
            status=e.status,
            email_type=e.email_type,
            error=e.error,
            deal_id=e.deal_id,
            deal_name=e.deal.deal_name if e.deal else None,
        )
        for e in emails
    ]


@router.get(
    "/{email_id}",
    response_model=EmailProcessingResult,
    summary="Get an email's extraction result",
    description="Returns the result snapshot recorded when the email was processed.",
    responses=error_responses(404, 409),
)
async def get_email_result(email_id: uuid.UUID, db: DbDep) -> EmailProcessingResult:
    email = await db.get(Email, email_id)
    if email is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Email not found")
    if email.result is None:
        raise ApiError(status.HTTP_409_CONFLICT, "not_processed", email.error or f"Email is {email.status}")
    return EmailProcessingResult.model_validate(email.result)
