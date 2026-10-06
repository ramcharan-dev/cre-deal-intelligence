"""End-to-end upload → persistence against the test database, with Claude replaced by a fake."""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.extraction.claude import ExtractionError
from app.extraction.matching import DealCandidate
from app.extraction.providers import get_extractor
from app.extraction.schemas import EmailExtraction
from app.main import app
from app.services.email_parser import ParsedEmail
from tests.emails import make_eml

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
WF_REVISED = make_eml(
    "Revised terms - Parkside",
    "Jane, following credit committee Wells Fargo is revising to S+260. All other terms unchanged.\n"
    "Property: 123 Main St, Austin.\n",
    message_id="<wf-2@wellsfargo.com>",
    sender="Tom Reed <tom.reed@wellsfargo.com>",
    date="Thu, 24 Sep 2026 10:00:00 -0500",
)


def submission_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "deal_submission",
        "summary": "Refinance request for Parkside Apartments",
        "deal_match": {"matched_deal_id": None, "confidence": "high", "reasoning": "No similar deal"},
        "deal_fields": [
            {"field": "property_name", "value": "Parkside Apartments", "source_text": "Parkside Apartments"},
            {
                "field": "property_address",
                "value": "123 Main Street",
                "source_text": "123 Main Street, Austin TX",
            },
            {"field": "city", "value": "Austin", "source_text": "Austin TX"},
            {"field": "property_type", "value": "multifamily", "source_text": "240-unit multifamily"},
            {"field": "units", "value": "240", "source_text": "240-unit"},
            {"field": "loan_amount_requested", "value": "12500000", "source_text": "$12.5MM refinance"},
            {"field": "occupancy_pct", "value": "94.5", "source_text": "Occupancy is 94.5%"},
            {
                "field": "sponsor_name",
                "value": "Oakline Capital",
                "source_text": "Sponsor is Oakline Capital",
            },
            {"field": "noi", "value": "900000", "source_text": "NOI of $900k"},  # not in email → rejected
        ],
        "quotes": [],
    }


def wf_quote_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "lender_quote",
        "summary": "Wells Fargo quote",
        "deal_match": {
            "matched_deal_id": candidates[0].id,
            "confidence": "high",
            "reasoning": "Reply in thread",
        },
        "deal_fields": [],
        "quotes": [
            {
                "lender_name": {"value": "Wells Fargo", "source_text": "Wells Fargo can offer"},
                "lender_contact_name": {"value": "Tom Reed", "source_text": "Tom Reed"},
                "lender_contact_email": {
                    "value": "tom.reed@wellsfargo.com",
                    "source_text": "tom.reed@wellsfargo.com",
                },
                "option_label": None,
                "fields": [
                    {"field": "loan_amount", "value": "12000000", "source_text": "$12,000,000"},
                    {"field": "ltv", "value": "65", "source_text": "65% LTV"},
                    {"field": "spread_bps", "value": "275", "source_text": "S+275"},
                    {"field": "term_months", "value": "36", "source_text": "36 month term"},
                    {"field": "recourse", "value": "non_recourse", "source_text": "non-recourse"},
                ],
            }
        ],
    }


def wf_revised_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "lender_quote",
        "summary": "Wells Fargo tightens spread",
        # Claude unsure here; the address match should still find the deal.
        "deal_match": {"matched_deal_id": None, "confidence": "low", "reasoning": "unclear"},
        "deal_fields": [
            {"field": "property_address", "value": "123 Main St", "source_text": "123 Main St, Austin"},
            {"field": "city", "value": "Austin", "source_text": "Austin"},
        ],
        "quotes": [
            {
                "lender_name": {"value": "Wells Fargo", "source_text": "Wells Fargo is revising"},
                "lender_contact_name": None,
                "lender_contact_email": None,
                "option_label": None,
                "fields": [{"field": "spread_bps", "value": "260", "source_text": "S+260"}],
            }
        ],
    }


class FakeExtractor:
    model = "fake-claude"

    def __init__(self) -> None:
        self.handler: Callable[[ParsedEmail, list[DealCandidate]], dict] | None = None
        self.calls = 0

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        self.calls += 1
        assert self.handler is not None
        return EmailExtraction.model_validate(self.handler(email, candidates))


@pytest.fixture
def fake(clean_db) -> Iterator[FakeExtractor]:
    fake = FakeExtractor()
    app.dependency_overrides[get_extractor] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


@pytest.fixture
def client(fake) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def upload(client: TestClient, fake: FakeExtractor, raw: bytes, handler) -> dict:
    fake.handler = handler
    r = client.post("/api/emails", files={"file": ("email.eml", raw, "message/rfc822")})
    assert r.status_code == 200, r.text
    return r.json()


def test_submission_creates_deal_with_sourced_fields(client, fake) -> None:
    result = upload(client, fake, SUBMISSION, submission_extraction)

    assert result["email"]["subject"] == "Parkside Apartments - $12.5MM refinance"
    assert result["email"]["sender"] == "Jane Broker <jane@brokerco.com>"
    assert result["deal"]["created"] is True
    assert result["deal"]["match_method"] == "new"
    assert result["deal"]["deal_name"] == "Parkside Apartments"
    fields = {f["field"]: f for f in result["deal_fields"]}
    assert fields["loan_amount_requested"]["value"] == "12500000"
    assert fields["loan_amount_requested"]["source_text"] == "$12.5MM refinance"
    assert "noi" not in fields
    assert [i["field"] for i in result["issues"]] == ["noi"]

    deal = client.get(f"/api/deals/{result['deal']['id']}").json()
    by_field = {f["field"]: f for f in deal["fields"]}
    assert by_field["units"]["value"] == "240"
    assert by_field["units"]["source_email_id"] == result["email"]["id"]
    assert by_field["units"]["source_text"] == "240-unit"


def test_thread_reply_quote_attaches_to_existing_deal(client, fake) -> None:
    deal_id = upload(client, fake, SUBMISSION, submission_extraction)["deal"]["id"]
    result = upload(client, fake, WF_QUOTE, wf_quote_extraction)

    assert result["deal"] == result["deal"] | {
        "id": deal_id,
        "created": False,
        "match_method": "email_thread",
    }
    [quote] = result["quotes"]
    assert quote["created"] is True and quote["lender"]["created"] is True
    assert quote["lender"]["name"] == "Wells Fargo"
    assert {f["field"]: f["value"] for f in quote["fields"]} == {
        "loan_amount": "12000000",
        "ltv": "65",
        "spread_bps": "275",
        "term_months": "36",
        "recourse": "non_recourse",
    }

    deal = client.get(f"/api/deals/{deal_id}").json()
    [q] = deal["quotes"]
    assert q["lender_contact_email"] == "tom.reed@wellsfargo.com"
    spread = next(f for f in q["fields"] if f["field"] == "spread_bps")
    assert (spread["value"], spread["source_text"]) == ("275", "S+275")
    assert spread["source_email_subject"] == "Re: Parkside Apartments - $12.5MM refinance"
    assert len(deal["emails"]) == 2


def test_revised_quote_updates_existing_quote_via_address_match(client, fake) -> None:
    deal_id = upload(client, fake, SUBMISSION, submission_extraction)["deal"]["id"]
    first = upload(client, fake, WF_QUOTE, wf_quote_extraction)["quotes"][0]
    result = upload(client, fake, WF_REVISED, wf_revised_extraction)

    assert (result["deal"]["id"], result["deal"]["match_method"]) == (deal_id, "address")
    [quote] = result["quotes"]
    assert quote["id"] == first["id"] and quote["created"] is False and quote["lender"]["created"] is False

    [q] = client.get(f"/api/deals/{deal_id}").json()["quotes"]
    values = {f["field"]: f for f in q["fields"]}
    assert values["spread_bps"]["value"] == "260"
    assert values["spread_bps"]["source_text"] == "S+260"
    assert values["ltv"]["value"] == "65"  # untouched by the revision
    assert len(client.get("/api/deals").json()) == 1


def test_older_email_does_not_overwrite_newer_value(client, fake) -> None:
    upload(client, fake, SUBMISSION, submission_extraction)
    upload(
        client,
        fake,
        WF_REVISED,
        lambda e, c: (
            wf_revised_extraction(e, c)
            | {  # sent Thu
                "deal_match": {"matched_deal_id": c[0].id, "confidence": "high", "reasoning": ""}
            }
        ),
    )
    result = upload(client, fake, WF_QUOTE, wf_quote_extraction)  # sent Tue, uploaded last

    spread = next(f for f in result["quotes"][0]["fields"] if f["field"] == "spread_bps")
    assert spread["applied"] is False
    [q] = client.get(f"/api/deals/{result['deal']['id']}").json()["quotes"]
    assert next(f for f in q["fields"] if f["field"] == "spread_bps")["value"] == "260"


def test_duplicate_upload_returns_stored_result_without_calling_claude(client, fake) -> None:
    first = upload(client, fake, SUBMISSION, submission_extraction)
    again = upload(client, fake, SUBMISSION, submission_extraction)
    assert fake.calls == 1
    assert again["duplicate"] is True and again["deal"]["id"] == first["deal"]["id"]
    assert client.get(f"/api/emails/{first['email']['id']}").json()["deal"]["id"] == first["deal"]["id"]


def test_non_deal_email_creates_nothing(client, fake) -> None:
    raw = make_eml("Lunch?", "Want to grab lunch Friday?", message_id="<lunch@x>")
    result = upload(
        client,
        fake,
        raw,
        lambda e, c: {
            "email_type": "other",
            "summary": "Lunch invite",
            "deal_match": {"matched_deal_id": None, "confidence": "high", "reasoning": "Not a deal"},
            "deal_fields": [],
            "quotes": [],
        },
    )
    assert result["deal"] is None
    assert client.get("/api/deals").json() == []


def test_extraction_failure_is_recorded_and_retryable(client, fake) -> None:
    def boom(e, c):
        raise ExtractionError("overloaded", "Claude is overloaded; try again shortly", retryable=True)

    fake.handler = boom
    r = client.post("/api/emails", files={"file": ("e.eml", SUBMISSION, "message/rfc822")})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert (detail["code"], detail["retryable"]) == ("overloaded", True)
    [email] = client.get("/api/emails").json()
    assert detail["email_id"] == email["id"]
    assert (email["status"], email["error"]) == (
        "failed",
        "[overloaded] Claude is overloaded; try again shortly",
    )

    retried = upload(client, fake, SUBMISSION, submission_extraction)
    assert retried["email"]["id"] == email["id"] and retried["deal"]["created"] is True


def test_missing_api_key_returns_503(clean_db) -> None:
    with TestClient(app) as c:  # real ClaudeExtractor; conftest blanks ANTHROPIC_API_KEY
        r = c.post("/api/emails", files={"file": ("e.eml", SUBMISSION, "message/rfc822")})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "not_configured"
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]["message"]


def test_rejects_non_email_upload(client) -> None:
    r = client.post("/api/emails", files={"file": ("notes.txt", b"hello there", "text/plain")})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_email"
