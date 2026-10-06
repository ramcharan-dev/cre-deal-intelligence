"""Gmail integration: OAuth → sync → processing against the test database, with Google and Claude faked.

Google is an `httpx.MockTransport` implementing the token, revoke and Gmail REST endpoints the client uses,
so no real credentials or network access are needed.
"""

import base64
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Iterator
from datetime import date
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.extraction.providers import get_extractor
from app.main import app
from app.services import google_client as google_module
from app.services.gmail_relevance import classify
from app.services.gmail_sync import build_query
from app.services.google_client import GMAIL_SCOPE, GoogleClient, get_google_client
from tests.emails import make_eml
from tests.test_email_ingestion import FakeExtractor, submission_extraction, wf_quote_extraction

ACCESS_TOKEN = "ya29.test-access-token-0123456789"
REFRESH_TOKEN = "1//0test-refresh-token-abcdefghijklmnop"
AUTH_CODE = "4/0test-auth-code-abcdefghijklmnopqrs"
CLIENT_SECRET = "GOCSPX-test-client-secret-value"
MAILBOX = "Priya.Raman@harborviewcap.com"

SUBMISSION = make_eml(
    "Parkside Apartments - $12.5MM refinance",
    "Team,\n\nWe're seeking a $12.5MM refinance for Parkside Apartments, a 240-unit multifamily property at "
    "123 Main Street, Austin TX. Sponsor is Oakline Capital. Occupancy is 94.5%.\n",
    message_id="<sub-1@brokerco.com>",
    date="Mon, 21 Sep 2026 09:00:00 -0500",
)
WF_QUOTE = make_eml(
    "Re: Parkside Apartments - $12.5MM refinance",
    "Jane,\n\nWells Fargo can offer $12,000,000 at 65% LTV, S+275, 36 month term, non-recourse.\n\n"
    "Tom Reed\ntom.reed@wellsfargo.com\n",
    message_id="<wf-1@wellsfargo.com>",
    sender="Tom Reed <tom.reed@wellsfargo.com>",
    date="Tue, 22 Sep 2026 10:00:00 -0500",
    in_reply_to="<sub-1@brokerco.com>",
)
LUNCH = make_eml(
    "Lunch on Friday?",
    "Anyone up for tacos at noon on Friday?\n",
    message_id="<lunch-1@ourfirm.com>",
    sender="Sam Office <sam@ourfirm.com>",
    date="Wed, 23 Sep 2026 12:00:00 -0500",
)


class FakeGoogle:
    """Google's OAuth token/revoke endpoints and the Gmail API, in memory."""

    def __init__(self) -> None:
        self.messages: dict[str, bytes] = {}  # insertion order = newest first
        self.calls: Counter[str] = Counter()
        self.token_requests: list[dict[str, str]] = []
        self.revoked: list[str] = []
        self.granted_scope = GMAIL_SCOPE
        self.refresh_error: str | None = None
        self.list_status = 200

    def add(self, gmail_id: str, raw: bytes) -> None:
        self.messages[gmail_id] = raw

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "oauth2.googleapis.com":
            form = dict(parse_qsl(request.content.decode()))
            if url.path == "/revoke":
                self.revoked.append(form["token"])
                return httpx.Response(200)
            self.token_requests.append(form)
            return self._token(form)

        assert url.host == "gmail.googleapis.com", url
        if request.headers.get("authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx.Response(401, json={"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        path = url.path.removeprefix("/gmail/v1/users/me")
        if path == "/profile":
            self.calls["profile"] += 1
            return httpx.Response(200, json={"emailAddress": MAILBOX, "messagesTotal": len(self.messages)})
        if path == "/messages":
            self.calls["list"] += 1
            if self.list_status != 200:
                return httpx.Response(
                    self.list_status, json={"error": {"code": self.list_status, "message": "x"}}
                )
            return self._list(url.params)
        self.calls["get"] += 1
        gmail_id = path.removeprefix("/messages/")
        assert url.params["format"] == "raw"
        if gmail_id not in self.messages:
            return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
        raw = self.messages[gmail_id]
        return httpx.Response(
            200,
            json={
                "id": gmail_id,
                "threadId": f"t-{gmail_id}",
                "labelIds": ["INBOX"],
                "snippet": "Preview &amp; more",
                "internalDate": "1790000000000",
                "raw": base64.urlsafe_b64encode(raw).decode().rstrip("="),
            },
        )

    def _token(self, form: dict[str, str]) -> httpx.Response:
        assert form["client_secret"] == CLIENT_SECRET
        if form["grant_type"] == "authorization_code":
            if form["code"] != AUTH_CODE:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "Bad Request"}
                )
            return httpx.Response(
                200,
                json={
                    "access_token": ACCESS_TOKEN,
                    "refresh_token": REFRESH_TOKEN,
                    "scope": self.granted_scope,
                    "expires_in": 3599,
                    "token_type": "Bearer",
                },
            )
        assert form["grant_type"] == "refresh_token"
        if self.refresh_error:
            return httpx.Response(
                400,
                json={"error": self.refresh_error, "error_description": "Token has been expired or revoked."},
            )
        assert form["refresh_token"] == REFRESH_TOKEN
        return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3599})

    def _list(self, params: httpx.QueryParams) -> httpx.Response:
        # Two per page, to exercise pagination.
        ids = list(self.messages)
        start = int(params.get("pageToken") or 0)
        size = min(int(params["maxResults"]), 2)
        page = ids[start : start + size]
        body: dict = {
            "messages": [{"id": i, "threadId": f"t-{i}"} for i in page],
            "resultSizeEstimate": len(ids),
        }
        if start + size < len(ids):
            body["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=body)


@pytest.fixture
def configured(monkeypatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", SecretStr(CLIENT_SECRET))
    monkeypatch.setattr(s, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(s, "frontend_url", "http://localhost:3001")

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(google_module.asyncio, "sleep", no_sleep)


@pytest.fixture
def google(clean_db) -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    app.dependency_overrides[get_google_client] = lambda: GoogleClient(
        transport=httpx.MockTransport(fake.handler)
    )
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def extractor(google) -> FakeExtractor:
    fake = FakeExtractor()
    app.dependency_overrides[get_extractor] = lambda: fake
    return fake


@pytest.fixture
def client(google, extractor) -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as c:
        yield c


def db_scalar(sql: str):
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        value = conn.execute(text(sql)).scalar()
    engine.dispose()
    return value


def connect(client: TestClient) -> httpx.Response:
    start = client.get("/api/gmail/oauth/start")
    assert start.status_code == 302, start.text
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return client.get("/api/gmail/oauth/callback", params={"code": AUTH_CODE, "state": state})


def outcome(response: httpx.Response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    location = urlsplit(response.headers["location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}" == "http://localhost:3001/integrations/gmail"
    )
    return {k: v[0] for k, v in parse_qs(location.query).items()}


def messages_by_subject(client: TestClient) -> dict[str, dict]:
    body = client.get("/api/gmail/messages").json()
    return {m["subject"]: m for m in body["messages"]}


# ---------------------------------------------------------------- configuration + OAuth


def test_not_configured(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "google_client_id", None)
    monkeypatch.setattr(get_settings(), "frontend_url", "http://localhost:3001")
    status = client.get("/api/gmail/connection").json()
    assert status == status | {"configured": False, "connected": False, "status": "not_connected"}
    r = client.get("/api/gmail/oauth/start")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "gmail_not_configured"
    assert outcome(client.get("/api/gmail/oauth/callback", params={"code": "x", "state": "y"})) == {
        "gmail_error": "gmail_not_configured"
    }


def test_oauth_start_requests_readonly_offline_access_with_pkce(configured, client) -> None:
    r = client.get("/api/gmail/oauth/start")
    assert r.status_code == 302
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert params["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert params["access_type"] == "offline" and params["prompt"] == "consent"
    assert params["response_type"] == "code" and params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "http://localhost:3001/api/gmail/oauth/callback"
    assert len(params["state"]) >= 32

    cookie = r.headers["set-cookie"]
    assert cookie.startswith("gmail_oauth=") and "HttpOnly" in cookie and "Path=/api/gmail/oauth" in cookie
    assert "samesite=lax" in cookie.lower() and "Max-Age=600" in cookie
    assert params["state"] not in cookie  # the cookie is encrypted


def test_full_flow_connect_sync_process(configured, google, extractor, client, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    google.add("g-quote", WF_QUOTE)
    google.add("g-lunch", LUNCH)
    google.add("g-sub", SUBMISSION)

    # --- OAuth
    assert outcome(connect(client)) == {"gmail": "connected"}
    [exchange] = google.token_requests
    assert exchange["grant_type"] == "authorization_code" and exchange["code_verifier"]

    status = client.get("/api/gmail/connection").json()
    assert status == status | {
        "configured": True,
        "connected": True,
        "status": "connected",
        "email_address": "priya.raman@harborviewcap.com",
        "scopes": [GMAIL_SCOPE],
        "last_sync": None,
    }
    stored = db_scalar("SELECT refresh_token_encrypted FROM gmail_accounts")
    assert REFRESH_TOKEN not in stored and decrypt(stored) == REFRESH_TOKEN

    # --- sync
    r = client.post("/api/gmail/sync", json={})
    assert r.status_code == 200, r.text
    sync = r.json()
    assert sync | {"synced_at": None} == {
        "scanned": 3,
        "new": 3,
        "duplicates": 0,
        "relevant_new": 2,
        "synced_at": None,
        "stats": {"scanned": 3, "relevant": 2, "processed": 0, "deals_updated": 0},
    }
    assert google.calls["list"] == 2  # paginated
    msgs = messages_by_subject(client)
    sub, quote, lunch = (
        msgs["Parkside Apartments - $12.5MM refinance"],
        msgs["Re: Parkside Apartments - $12.5MM refinance"],
        msgs["Lunch on Friday?"],
    )
    assert sub["status"] == "pending" and sub["email_id"] and sub["category"] == "financing"
    assert quote["status"] == "pending" and quote["relevance"] >= 40
    assert lunch == lunch | {
        "status": "skipped",
        "email_id": None,
        "category": "other",
        "snippet": "Preview & more",
    }
    assert sub["sender_email"] == "jane@brokerco.com" and sub["sent_at"].startswith("2026-09-21")

    # Normalized into the existing email model, visible on the emails page.
    emails = {e["subject"]: e for e in client.get("/api/emails").json()}
    assert emails["Parkside Apartments - $12.5MM refinance"]["status"] == "received"
    assert "Lunch on Friday?" not in emails

    detail = client.get(f"/api/gmail/messages/{sub['id']}").json()
    assert "240-unit multifamily" in detail["body_text"] and detail["to"] == ["Deal Team <deals@ourfirm.com>"]
    assert detail["gmail_url"].endswith("#all/g-sub")
    assert client.get(f"/api/gmail/messages/{lunch['id']}").json()["body_text"] is None

    # --- duplicate protection: a second sync fetches nothing again
    gets = google.calls["get"]
    again = client.post("/api/gmail/sync", json={}).json()
    assert (again["scanned"], again["new"], again["duplicates"]) == (3, 0, 3)
    assert google.calls["get"] == gets
    assert db_scalar("SELECT count(*) FROM gmail_messages") == 3
    assert db_scalar("SELECT count(*) FROM emails") == 2

    # --- processing with the existing pipeline
    extractor.handler = submission_extraction
    r = client.post(f"/api/gmail/messages/{sub['id']}/process")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["deal"]["created"] is True and body["result"]["duplicate"] is False
    assert body["result"]["email"]["id"] == sub["email_id"]  # the synced record, not a new one
    assert body["message"] | {"deal_id": None} == sub | {
        "status": "processed",
        "email_type": "deal_submission",
        "deal_name": "Parkside Apartments",
        "deal_id": None,
    }

    extractor.handler = wf_quote_extraction
    quote_result = client.post(f"/api/gmail/messages/{quote['id']}/process").json()["result"]
    assert quote_result["deal"]["match_method"] == "email_thread"
    assert extractor.calls == 2

    # Processing again returns the stored result without calling Claude.
    again = client.post(f"/api/gmail/messages/{sub['id']}/process").json()
    assert again["result"]["duplicate"] is True and extractor.calls == 2

    stats = client.get("/api/gmail/messages").json()["stats"]
    assert stats == {"scanned": 3, "relevant": 2, "processed": 2, "deals_updated": 1}
    deal = client.get(f"/api/deals/{body['result']['deal']['id']}").json()
    assert deal["quotes"][0]["lender_name"] == "Wells Fargo"

    # --- tokens never leak into logs or responses
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, AUTH_CODE, CLIENT_SECRET):
        assert secret not in caplog.text
    assert REFRESH_TOKEN not in json.dumps(status) and ACCESS_TOKEN not in r.text


def test_processing_a_skipped_message_fetches_it_from_gmail(configured, google, extractor, client) -> None:
    google.add("g-lunch", LUNCH)
    connect(client)
    client.post("/api/gmail/sync", json={})
    [lunch] = client.get("/api/gmail/messages").json()["messages"]
    gets = google.calls["get"]

    extractor.handler = lambda email, candidates: {
        "email_type": "other",
        "summary": "Lunch plans",
        "deal_match": {"matched_deal_id": None, "confidence": "high", "reasoning": "Not a deal"},
        "deal_fields": [],
        "quotes": [],
    }
    r = client.post(f"/api/gmail/messages/{lunch['id']}/process")
    assert r.status_code == 200, r.text
    assert google.calls["get"] == gets + 1
    assert r.json()["message"]["status"] == "processed" and r.json()["result"]["deal"] is None


def test_sync_links_a_message_already_uploaded_as_eml(configured, google, extractor, client) -> None:
    extractor.handler = submission_extraction
    uploaded = client.post("/api/emails", files={"file": ("sub.eml", SUBMISSION, "message/rfc822")}).json()
    google.add("g-sub", SUBMISSION)
    connect(client)
    client.post("/api/gmail/sync", json={})
    [message] = client.get("/api/gmail/messages").json()["messages"]
    assert message["email_id"] == uploaded["email"]["id"] and message["status"] == "processed"
    assert db_scalar("SELECT count(*) FROM emails") == 1


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"error": "access_denied", "state": "s"}, "access_denied"),
        ({"code": AUTH_CODE, "state": "forged"}, "invalid_state"),
    ],
)
def test_callback_rejects_denied_or_forged_requests(configured, google, client, params, error) -> None:
    client.get("/api/gmail/oauth/start")  # sets a valid cookie for a different state
    assert outcome(client.get("/api/gmail/oauth/callback", params=params)) == {"gmail_error": error}
    assert google.token_requests == []
    assert client.get("/api/gmail/connection").json()["connected"] is False


def test_callback_without_state_cookie_is_rejected(configured, google, client) -> None:
    r = client.get("/api/gmail/oauth/callback", params={"code": AUTH_CODE, "state": "abc"})
    assert outcome(r) == {"gmail_error": "invalid_state"} and google.token_requests == []


def test_callback_requires_gmail_scope(configured, google, client) -> None:
    google.granted_scope = "openid"
    assert outcome(connect(client)) == {"gmail_error": "scope_denied"}
    assert google.revoked == [ACCESS_TOKEN]
    assert client.get("/api/gmail/connection").json()["connected"] is False


def test_callback_rejected_code(configured, google, client) -> None:
    start = client.get("/api/gmail/oauth/start")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    r = client.get(
        "/api/gmail/oauth/callback", params={"code": "4/0expired-code-abcdefghijklmnop", "state": state}
    )
    assert outcome(r) == {"gmail_error": "oauth_failed"}


# ---------------------------------------------------------------- disconnect + failures


def test_disconnect_revokes_and_forgets_credentials(configured, google, extractor, client) -> None:
    google.add("g-sub", SUBMISSION)
    connect(client)
    client.post("/api/gmail/sync", json={})

    r = client.delete("/api/gmail/connection")
    assert r.status_code == 200 and r.json() == {"disconnected": True, "revoked": True}
    assert google.revoked == [REFRESH_TOKEN]
    assert db_scalar("SELECT count(*) FROM gmail_accounts") == 0
    assert db_scalar("SELECT count(*) FROM gmail_messages") == 0
    assert db_scalar("SELECT count(*) FROM emails") == 1  # normalized emails are kept
    assert client.get("/api/gmail/connection").json()["status"] == "not_connected"
    assert client.delete("/api/gmail/connection").json() == {"disconnected": False, "revoked": False}

    r = client.post("/api/gmail/sync", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gmail_not_connected"


def test_revoked_grant_requires_reconnect(configured, google, client) -> None:
    connect(client)
    google.refresh_error = "invalid_grant"
    r = client.post("/api/gmail/sync", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gmail_reauth_required"
    assert client.get("/api/gmail/connection").json()["status"] == "reauth_required"

    google.refresh_error = None
    assert outcome(connect(client)) == {"gmail": "connected"}
    assert client.get("/api/gmail/connection").json()["status"] == "connected"


@pytest.mark.parametrize(
    ("status", "code", "http"),
    [
        (429, "gmail_rate_limited", 429),
        (403, "gmail_permission_denied", 503),
        (500, "gmail_upstream_error", 502),
    ],
)
def test_gmail_api_errors_map_to_http(configured, google, client, status, code, http) -> None:
    connect(client)
    google.list_status = status
    r = client.post("/api/gmail/sync", json={})
    assert r.status_code == http and r.json()["detail"]["code"] == code
    assert r.json()["detail"]["retryable"] is (status in (429, 500))


def test_sync_rejects_inverted_date_range(configured, google, client) -> None:
    connect(client)
    r = client.post("/api/gmail/sync", json={"after": "2026-09-30", "before": "2026-09-01"})
    assert r.status_code == 422


# ---------------------------------------------------------------- units


def test_build_query() -> None:
    assert build_query(None, None, today=date(2026, 10, 1)).endswith("after:2026/09/01")
    q = build_query(date(2026, 9, 1), date(2026, 9, 30))
    assert "after:2026/09/01" in q and "before:2026/10/01" in q and "-category:promotions" in q


@pytest.mark.parametrize(
    ("subject", "body", "category", "relevant"),
    [
        (
            "Term sheet – 1850 Riverside Drive",
            "Signed term sheet: $50,000,000 at 64% LTV, 3 years IO.",
            "term_sheet",
            True,
        ),
        (
            "Indicative terms",
            "SOFR + 285 bps, 70% LTV, 1.00% origination fee, non-recourse.",
            "lender_quote",
            True,
        ),
        (
            "Riverbend – rent roll and T12",
            "Occupancy 96.1%, NOI $4,162,000. 212-unit multifamily.",
            "deal_update",
            True,
        ),
        (
            "Following up – Harbor Point debt request",
            "Circling back on Harbor Point. Credit wants the tenant estoppels and Phase I before we issue "
            "terms on the acquisition loan.",
            "follow_up",
            True,
        ),
        ("Your order has shipped", "Your package arrives Tuesday. Save 20% on $50.", "other", False),
        (
            "Weekly CRE newsletter",
            "Office vacancy rises. Register for our webinar. Unsubscribe.",
            "other",
            False,
        ),
    ],
)
def test_classify(subject, body, category, relevant) -> None:
    got_category, relevance = classify(subject, body)
    assert got_category == category
    assert (relevance >= get_settings().gmail_relevance_threshold) is relevant


def test_pkce_challenge_matches_verifier(configured, google, client) -> None:
    start = client.get("/api/gmail/oauth/start")
    query = parse_qs(urlsplit(start.headers["location"]).query)
    client.get("/api/gmail/oauth/callback", params={"code": AUTH_CODE, "state": query["state"][0]})
    verifier = google.token_requests[0]["code_verifier"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"][0] == expected
