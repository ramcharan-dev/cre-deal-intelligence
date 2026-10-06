"""Google OAuth 2.0 (web server flow + PKCE) and the read-only Gmail REST calls the sync needs.

Plain httpx, so tests can substitute an `httpx.MockTransport`. Tokens are never logged or put in error
messages; Google's own error text is passed through `redact` as a backstop.
"""

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.core.logging import redact

log = logging.getLogger(__name__)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

GmailErrorCode = Literal[
    "gmail_not_configured",  # client id/secret or encryption key missing
    "gmail_not_connected",  # no mailbox connected
    "gmail_reauth_required",  # refresh token revoked/expired: reconnect
    "gmail_permission_denied",  # 403: Gmail API disabled for the project, or scope not granted
    "gmail_rate_limited",  # 429
    "gmail_upstream_error",  # other 4xx/5xx from Google
    "gmail_unreachable",  # network error
    "gmail_timeout",
    "oauth_failed",  # authorization code exchange rejected
]


class GmailError(RuntimeError):
    """A Google call failed. `message` is safe to show: no tokens, no email content."""

    def __init__(self, code: GmailErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code: GmailErrorCode = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    scopes: list[str]


@dataclass(frozen=True)
class RawMessage:
    id: str
    thread_id: str | None
    label_ids: list[str]
    snippet: str
    internal_date: datetime | None
    raw: bytes = field(repr=False)  # RFC 822 source


def _google_error(response: httpx.Response) -> tuple[str, str]:
    """(reason, message) from an OAuth (`{"error": "...", "error_description"}`) or API error body."""
    try:
        body: Any = response.json()
    except ValueError:
        return "", f"HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error, redact(str(body.get("error_description") or error))
    if isinstance(error, dict):
        reasons = [e.get("reason", "") for e in error.get("errors", []) if isinstance(e, dict)]
        return (reasons[0] if reasons else str(error.get("status", ""))), redact(
            str(error.get("message", ""))
        )
    return "", f"HTTP {response.status_code}"


def _raise_for_status(response: httpx.Response, what: str) -> None:
    if response.is_success:
        return
    status = response.status_code
    reason, message = _google_error(response)
    log.warning("google %s failed status=%d reason=%s", what, status, reason or "-")
    if status == 401 or reason == "invalid_grant":
        raise GmailError(
            "gmail_reauth_required", "Gmail access was revoked or has expired. Reconnect Gmail to continue."
        )
    if status == 403:
        raise GmailError(
            "gmail_permission_denied",
            f"Google denied access while trying to {what} ({message or reason}). Make sure the Gmail API is "
            "enabled for the OAuth client's Google Cloud project and the read-only Gmail permission was granted.",
        )
    if status == 429:
        raise GmailError(
            "gmail_rate_limited", "Gmail rate limit reached. Try again in a minute.", retryable=True
        )
    raise GmailError(
        "gmail_upstream_error",
        f"Google returned HTTP {status} while trying to {what}" + (f": {message}" if message else "."),
        retryable=status >= 500,
    )


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class GoogleClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=get_settings().gmail_timeout_seconds)

    async def _send(
        self, client: httpx.AsyncClient, what: str, method: str, url: str, **kw: Any
    ) -> httpx.Response:
        """One request, retried once on 429/5xx. Raises GmailError for anything but 2xx (and 404 on GETs)."""
        for attempt in (1, 2):
            try:
                response = await client.request(method, url, **kw)
            except httpx.TimeoutException as exc:
                raise GmailError(
                    "gmail_timeout", f"Google did not respond in time ({what}).", retryable=True
                ) from exc
            except httpx.TransportError as exc:
                raise GmailError(
                    "gmail_unreachable", f"Could not reach Google ({what}).", retryable=True
                ) from exc
            if attempt == 1 and (response.status_code == 429 or response.status_code >= 500):
                await asyncio.sleep(1)
                continue
            break
        if not (method == "GET" and response.status_code == 404):
            _raise_for_status(response, what)
        return response

    # ------------------------------------------------------------ OAuth

    def authorization_url(self, state: str, code_challenge: str) -> str:
        s = get_settings()
        params = {
            "client_id": s.google_client_id,
            "redirect_uri": s.gmail_redirect_uri,
            "response_type": "code",
            "scope": GMAIL_SCOPE,
            "access_type": "offline",  # issue a refresh token
            "prompt": "consent",  # ...every time, so reconnecting always yields a fresh one
            "include_granted_scopes": "true",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> TokenGrant:
        s = get_settings()
        assert s.google_client_secret is not None
        data = {
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "redirect_uri": s.gmail_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        }
        async with self._client() as client:
            try:
                response = await self._send(
                    client, "exchange the authorization code", "POST", TOKEN_URL, data=data
                )
            except GmailError as exc:
                if exc.code == "gmail_reauth_required":  # invalid_grant: code expired, reused or mismatched
                    raise GmailError(
                        "oauth_failed", "Google rejected the authorization code. Try connecting again."
                    ) from exc
                raise
        body = response.json()
        return TokenGrant(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            scopes=str(body.get("scope", "")).split(),
        )

    async def refresh_access_token(self, refresh_token: str) -> str:
        s = get_settings()
        assert s.google_client_secret is not None
        data = {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with self._client() as client:
            response = await self._send(client, "refresh the access token", "POST", TOKEN_URL, data=data)
        return response.json()["access_token"]

    async def revoke(self, token: str) -> bool:
        """Best effort: disconnecting must succeed locally even if Google is unreachable."""
        try:
            async with self._client() as client:
                response = await client.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError as exc:
            log.warning("google token revoke failed (%s)", type(exc).__name__)
            return False
        if not response.is_success:  # 400 invalid_token: already revoked
            log.info("google token revoke returned status=%d", response.status_code)
        return response.is_success

    # ------------------------------------------------------------ Gmail

    async def get_profile_email(self, access_token: str) -> str:
        async with self._client() as client:
            response = await self._send(
                client, "read the Gmail profile", "GET", f"{GMAIL_API}/profile", headers=_auth(access_token)
            )
        _raise_for_status(response, "read the Gmail profile")  # a 404 here is an error too
        return str(response.json()["emailAddress"]).lower()

    async def list_message_ids(self, access_token: str, query: str, limit: int) -> list[str]:
        """Newest first, at most `limit`."""
        ids: list[str] = []
        page_token: str | None = None
        async with self._client() as client:
            while len(ids) < limit:
                params = {"q": query, "maxResults": min(500, limit - len(ids))}
                if page_token:
                    params["pageToken"] = page_token
                response = await self._send(
                    client,
                    "list Gmail messages",
                    "GET",
                    f"{GMAIL_API}/messages",
                    params=params,
                    headers=_auth(access_token),
                )
                _raise_for_status(response, "list Gmail messages")
                body = response.json()
                ids += [m["id"] for m in body.get("messages", [])]
                page_token = body.get("nextPageToken")
                if not page_token:
                    break
        return ids[:limit]

    async def get_raw_messages(self, access_token: str, ids: list[str]) -> list[RawMessage]:
        """Fetch full RFC 822 sources concurrently. Messages deleted since listing (404) are skipped."""
        semaphore = asyncio.Semaphore(get_settings().gmail_fetch_concurrency)
        async with self._client() as client:

            async def fetch(message_id: str) -> RawMessage | None:
                async with semaphore:
                    return await self._get_raw(client, access_token, message_id)

            results = await asyncio.gather(*(fetch(i) for i in ids))
        return [r for r in results if r is not None]

    async def get_raw_message(self, access_token: str, message_id: str) -> RawMessage | None:
        async with self._client() as client:
            return await self._get_raw(client, access_token, message_id)

    async def _get_raw(
        self, client: httpx.AsyncClient, access_token: str, message_id: str
    ) -> RawMessage | None:
        response = await self._send(
            client,
            "fetch a Gmail message",
            "GET",
            f"{GMAIL_API}/messages/{message_id}",
            params={"format": "raw"},
            headers=_auth(access_token),
        )
        if response.status_code == 404:
            return None
        body = response.json()
        internal = body.get("internalDate")
        return RawMessage(
            id=body["id"],
            thread_id=body.get("threadId"),
            label_ids=list(body.get("labelIds", [])),
            snippet=body.get("snippet", ""),
            internal_date=datetime.fromtimestamp(int(internal) / 1000, UTC) if internal else None,
            raw=_b64url_decode(body["raw"]),
        )


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def get_google_client() -> GoogleClient:
    """FastAPI dependency; tests override it with a client on a mock transport."""
    return GoogleClient()
