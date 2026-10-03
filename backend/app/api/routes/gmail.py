"""Gmail integration: Google OAuth 2.0 (read-only), connection status, sync and processing."""

import base64
import hashlib
import json
import logging
import secrets
import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import EXTRACTION_STATUS, ApiError, error_responses
from app.api.schemas import (
    GmailConnection,
    GmailDisconnectResult,
    GmailLastSync,
    GmailMessageDetail,
    GmailMessageList,
    GmailMessageOut,
    GmailProcessResult,
    GmailStatsOut,
    GmailSyncRequest,
    GmailSyncResult,
)
from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.db.session import get_db
from app.extraction.claude import Extractor, get_extractor
from app.models import GmailAccount, GmailMessage
from app.services import gmail_sync
from app.services.email_ingestion import IngestionError
from app.services.email_parser import EmailParseError
from app.services.google_client import GMAIL_SCOPE, GmailError, GoogleClient, get_google_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gmail", tags=["gmail"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
GoogleDep = Annotated[GoogleClient, Depends(get_google_client)]

STATE_COOKIE = "gmail_oauth"
STATE_COOKIE_PATH = "/api/gmail/oauth"
STATE_TTL_SECONDS = 600

NOT_CONFIGURED = (
    "Gmail integration is not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
    "TOKEN_ENCRYPTION_KEY, then restart the backend."
)


def _require_configured() -> None:
    if not get_settings().gmail_configured:
        raise GmailError("gmail_not_configured", NOT_CONFIGURED)


def _frontend_redirect(**params: str) -> RedirectResponse:
    url = f"{get_settings().frontend_base_url}/integrations/gmail?{urlencode(params)}"
    response = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return response


def _last_sync(account: GmailAccount) -> GmailLastSync | None:
    if account.last_synced_at is None:
        return None
    return GmailLastSync(
        at=account.last_synced_at, scanned=account.last_sync_scanned, new=account.last_sync_new
    )


def _message_out(m: GmailMessage) -> GmailMessageOut:
    email = m.email
    return GmailMessageOut(
        id=m.id,
        gmail_id=m.gmail_id,
        thread_id=m.thread_id,
        subject=m.subject,
        sender_name=m.sender_name,
        sender_email=m.sender_email,
        sent_at=m.sent_at,
        snippet=m.snippet,
        category=m.category,  # type: ignore[arg-type]
        relevance=m.relevance,
        status=gmail_sync.message_status(m),
        error=email.error if email and email.status == "failed" else None,
        attachment_names=m.attachment_names,
        email_id=m.email_id,
        email_type=email.email_type if email else None,
        deal_id=email.deal_id if email else None,
        deal_name=email.deal.deal_name if email and email.deal else None,
    )


def _addresses(recipients: list[dict[str, str | None]]) -> list[str]:
    return [f"{r['name']} <{r['email']}>" if r.get("name") else str(r["email"]) for r in recipients]


async def _stats(db: AsyncSession, account: GmailAccount) -> GmailStatsOut:
    s = await gmail_sync.account_stats(db, account)
    return GmailStatsOut(
        scanned=s.scanned, relevant=s.relevant, processed=s.processed, deals_updated=s.deals_updated
    )


async def _get_message_or_404(db: AsyncSession, account: GmailAccount, message_id: uuid.UUID) -> GmailMessage:
    message = await gmail_sync.get_message(db, account, message_id)
    if message is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Gmail message not found")
    return message


MessageIdPath = Annotated[uuid.UUID, Path(description="`id` of a synced message (not the Gmail id)")]

# ---------------------------------------------------------------- OAuth


@router.get(
    "/oauth/start",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    summary="Start Google OAuth (browser navigation)",
    description=(
        "Open this URL in the browser (not via XHR). Redirects to Google's consent screen requesting only "
        f"`{GMAIL_SCOPE}` with offline access, so Google issues a refresh token.\n\n"
        "A random `state` and a PKCE code verifier are stored in an encrypted, HttpOnly, 10-minute cookie "
        f"(`{STATE_COOKIE}`, path `{STATE_COOKIE_PATH}`) and checked by the callback."
    ),
    responses={
        302: {"description": "Redirect to `accounts.google.com`; sets the OAuth state cookie"},
        **error_responses(503, d503="Gmail integration is not configured (`gmail_not_configured`)"),
    },
)
async def oauth_start(google: GoogleDep) -> RedirectResponse:
    _require_configured()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = RedirectResponse(google.authorization_url(state, challenge), status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE,
        encrypt(json.dumps({"state": state, "verifier": verifier})),
        max_age=STATE_TTL_SECONDS,
        path=STATE_COOKIE_PATH,
        httponly=True,
        samesite="lax",  # sent on Google's top-level redirect back to the callback
        secure=get_settings().gmail_redirect_uri.startswith("https://"),
    )
    return response


@router.get(
    "/oauth/callback",
    response_class=RedirectResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    summary="Google OAuth redirect URI",
    description=(
        "Google redirects here after consent. Verifies `state` against the cookie, exchanges the code "
        "(with the PKCE verifier) for tokens, checks the Gmail read-only scope was granted, reads the "
        "mailbox address and stores the refresh token encrypted. Access tokens are never stored.\n\n"
        "Always ends with a redirect to the frontend's `/integrations/gmail`, with `?gmail=connected` on "
        "success or `?gmail_error=<code>` where code is one of `access_denied`, `oauth_error`, `invalid_state`, "
        "`invalid_request`, `scope_denied`, `no_refresh_token`, `oauth_failed`, `gmail_not_configured`, "
        "`gmail_permission_denied`, `gmail_rate_limited`, `gmail_upstream_error`, `gmail_unreachable`, "
        "`gmail_timeout`."
    ),
    responses={303: {"description": "Redirect to the frontend Gmail page with the outcome"}},
)
async def oauth_callback(
    request: Request,
    db: DbDep,
    google: GoogleDep,
    code: Annotated[str | None, Query(description="Authorization code from Google")] = None,
    state: Annotated[str | None, Query(description="Must match the state cookie")] = None,
    error: Annotated[
        str | None, Query(description="Set by Google when consent failed or was declined")
    ] = None,
) -> RedirectResponse:
    if not get_settings().gmail_configured:
        return _frontend_redirect(gmail_error="gmail_not_configured")
    if error:
        log.info("gmail oauth returned error=%s", error if error.isidentifier() else "other")
        return _frontend_redirect(gmail_error="access_denied" if error == "access_denied" else "oauth_error")

    cookie = request.cookies.get(STATE_COOKIE)
    saved = decrypt(cookie, max_age_seconds=STATE_TTL_SECONDS) if cookie else None
    data = json.loads(saved) if saved else {}
    if not state or not data or not secrets.compare_digest(state, data.get("state", "")):
        log.warning("gmail oauth callback with missing, expired or mismatched state")
        return _frontend_redirect(gmail_error="invalid_state")
    if not code:
        return _frontend_redirect(gmail_error="invalid_request")

    try:
        grant = await google.exchange_code(code, data["verifier"])
        await gmail_sync.connect_account(db, google, grant)
    except gmail_sync.ScopeNotGrantedError:
        return _frontend_redirect(gmail_error="scope_denied")
    except gmail_sync.NoRefreshTokenError:
        return _frontend_redirect(gmail_error="no_refresh_token")
    except GmailError as exc:
        return _frontend_redirect(gmail_error=exc.code)
    return _frontend_redirect(gmail="connected")


# ---------------------------------------------------------------- connection


@router.get(
    "/connection",
    response_model=GmailConnection,
    summary="Gmail connection status",
    description="Whether the integration is configured, which mailbox is connected, and the last sync.",
)
async def get_connection(db: DbDep) -> GmailConnection:
    configured = get_settings().gmail_configured
    account = await gmail_sync.get_account(db)
    if account is None:
        return GmailConnection(configured=configured, connected=False, status="not_connected")
    return GmailConnection(
        configured=configured,
        connected=True,
        status="reauth_required" if account.status == "reauth_required" else "connected",
        email_address=account.email_address,
        scopes=account.scopes,
        connected_at=account.connected_at,
        last_sync=_last_sync(account),
    )


@router.delete(
    "/connection",
    response_model=GmailDisconnectResult,
    summary="Disconnect Gmail",
    description=(
        "Revokes the refresh token at Google (best effort) and deletes the stored credentials and the "
        "synced-message index. Emails already stored for processing, and everything extracted from them, "
        "are kept. Idempotent."
    ),
)
async def disconnect(db: DbDep, google: GoogleDep) -> GmailDisconnectResult:
    account = await gmail_sync.get_account(db)
    if account is None:
        return GmailDisconnectResult(disconnected=False, revoked=False)
    revoked = await gmail_sync.disconnect_account(db, google, account)
    return GmailDisconnectResult(disconnected=True, revoked=revoked)


# ---------------------------------------------------------------- sync + messages


@router.post(
    "/sync",
    response_model=GmailSyncResult,
    summary="Sync Gmail messages",
    description=(
        "Scans the newest `max_messages` in the date window (chats, Promotions and Social excluded). "
        "Messages whose Gmail id is already stored are skipped without being fetched. New messages are "
        "fetched in RFC 822 form, parsed with the same parser as uploads, and scored for CRE relevance "
        "with keyword rules (no AI call). Relevant messages are stored as `emails` records with status "
        "`received`, ready for processing; the rest are indexed with metadata only."
    ),
    responses=error_responses(
        409,
        422,
        429,
        502,
        503,
        504,
        d409="Gmail not connected (`gmail_not_connected`) or the grant was revoked (`gmail_reauth_required`)",
        d422="Invalid request body (e.g. `after` later than `before`)",
        d429="Gmail rate limit (`gmail_rate_limited`, retryable)",
        d502="Google API error or unreachable (`gmail_upstream_error`, `gmail_unreachable`)",
        d503="Not configured, or the Gmail API is disabled / scope missing (`gmail_permission_denied`)",
        d504="Google timed out (`gmail_timeout`, retryable)",
    ),
)
async def sync(body: GmailSyncRequest, db: DbDep, google: GoogleDep) -> GmailSyncResult:
    _require_configured()
    account = await gmail_sync.require_account(db)
    outcome = await gmail_sync.sync_mailbox(
        db, google, account, after=body.after, before=body.before, max_messages=body.max_messages
    )
    assert account.last_synced_at is not None
    return GmailSyncResult(
        scanned=outcome.scanned,
        new=outcome.new,
        duplicates=outcome.duplicates,
        relevant_new=outcome.relevant_new,
        synced_at=account.last_synced_at,
        stats=await _stats(db, account),
    )


@router.get(
    "/messages",
    response_model=GmailMessageList,
    summary="List synced Gmail messages",
    description="Newest first, with processing status, linked deal, and sync statistics.",
    responses=error_responses(409, d409="Gmail not connected (`gmail_not_connected`)"),
)
async def list_messages(
    db: DbDep, limit: Annotated[int, Query(ge=1, le=1000, description="Maximum number of messages")] = 500
) -> GmailMessageList:
    account = await gmail_sync.require_account(db)
    messages = await gmail_sync.list_messages(db, account, limit)
    return GmailMessageList(
        messages=[_message_out(m) for m in messages],
        stats=await _stats(db, account),
        last_sync=_last_sync(account),
    )


@router.get(
    "/messages/{message_id}",
    response_model=GmailMessageDetail,
    summary="Get a synced Gmail message",
    description="Includes the plain-text body for stored (relevant) messages; skipped ones have only the snippet.",
    responses=error_responses(404, 409, d409="Gmail not connected (`gmail_not_connected`)"),
)
async def get_message(message_id: MessageIdPath, db: DbDep) -> GmailMessageDetail:
    account = await gmail_sync.require_account(db)
    message = await _get_message_or_404(db, account, message_id)
    email = message.email
    return GmailMessageDetail(
        **_message_out(message).model_dump(),
        to=_addresses(email.recipients.get("to", [])) if email else [],
        cc=_addresses(email.recipients.get("cc", [])) if email else [],
        body_text=email.body_text if email else None,
        gmail_url=f"https://mail.google.com/mail/?authuser={account.email_address}#all/{message.gmail_id}",
    )


@router.post(
    "/messages/{message_id}/process",
    response_model=GmailProcessResult,
    summary="Process a Gmail message with AI",
    description=(
        "Runs the existing email intelligence pipeline (parse → Claude extraction → validation → deal "
        "matching → persistence) on the message's RFC 822 source, exactly as for an uploaded `.eml`. "
        "Skipped messages are fetched from Gmail first. An already processed message returns the stored "
        "result with `duplicate: true` and no Claude call; a failed one is re-processed."
    ),
    responses=error_responses(
        400,
        404,
        409,
        422,
        429,
        502,
        503,
        504,
        d400="The message has no readable text body (`invalid_email`)",
        d404="Unknown message id, or the message was deleted from Gmail (`gmail_message_gone`)",
        d409="Gmail not connected or the grant was revoked (only when the message must be fetched)",
        d429="Claude or Gmail rate limit (retryable)",
        d502="Claude or Google API error / unreachable (see `code`)",
        d503="Claude or Gmail not configured, key rejected, or overloaded (see `code`)",
        d504="Claude or Google timed out (retryable)",
    ),
)
async def process_message(
    message_id: MessageIdPath,
    db: DbDep,
    google: GoogleDep,
    extractor: Annotated[Extractor, Depends(get_extractor)],
) -> GmailProcessResult:
    account = await gmail_sync.require_account(db)
    message = await _get_message_or_404(db, account, message_id)
    if message.email_id is None:
        _require_configured()
    try:
        result = await gmail_sync.process_message(db, google, extractor, account, message)
    except gmail_sync.MessageGoneError as exc:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "gmail_message_gone", "The message no longer exists in Gmail"
        ) from exc
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
    message = await _get_message_or_404(db, account, message_id)  # re-read: the pipeline linked a deal
    return GmailProcessResult(message=_message_out(message), result=result)
