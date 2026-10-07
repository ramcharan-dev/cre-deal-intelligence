# ruff: noqa: F811  (pytest fixtures imported from test_gmail are redefined as test arguments)
"""Gmail → Process with AI → Deal → Dashboard → Quote comparison, with a fake extractor and a fake Gmail API.

Email dates are relative to today so status/validity/due-date assertions do not age.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.extraction.matching import DealCandidate
from app.extraction.schemas import EmailExtraction
from app.models import Deal
from app.services.deal_dashboard import parse_due_date
from app.services.email_ingestion import ingest_email
from app.services.email_parser import ParsedEmail
from tests.emails import make_eml
from tests.test_gmail import (  # noqa: F401  (fixtures)
    client,
    configured,
    connect,
    extractor,
    google,
    messages_by_subject,
)

NOW = datetime.now(UTC).replace(microsecond=0)
TODAY = NOW.date()


def _date(days_ago: int) -> str:
    return format_datetime(NOW - timedelta(days=days_ago))


def _md(d: date) -> str:
    return f"{d:%B} {d.day}"


TERMS_DUE = TODAY + timedelta(days=5)
SITE_VISIT = TODAY + timedelta(days=8)
WF_EXPIRES = TODAY + timedelta(days=20)


def _submission() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Jane Broker <jane@brokerco.com>"
    msg["To"] = (
        "Tom Reed <tom.reed@wellsfargo.com>, Ann Lee <ann.lee@pacificbank.com>, Raj Patel <raj@harborlife.com>"
    )
    msg["Cc"] = "Mike Broker <mike@brokerco.com>"
    msg["Subject"] = "Parkside Apartments - $12.5MM refinance loan request"
    msg["Date"] = _date(3)
    msg["Message-ID"] = "<dash-sub@brokerco.com>"
    msg.set_content(
        "Team,\n\nAttached is the package for Parkside Apartments, a 240-unit multifamily property at "
        "123 Main Street, Austin TX. We're seeking a $12.5MM refinance. Sponsor is Oakline Capital.\n"
        f"Please send indicative terms by {_md(TERMS_DUE)}.\n"
        f"The site visit is scheduled for {_md(SITE_VISIT)} at 10am.\n"
    )
    msg.add_attachment(b"x", maintype="application", subtype="pdf", filename="Parkside_OM.pdf")
    msg.add_attachment(
        b"x", maintype="application", subtype="octet-stream", filename="Parkside Rent Roll.xlsx"
    )
    return msg.as_bytes()


SUBMISSION = _submission()

WF_QUOTE = make_eml(
    "Re: Parkside Apartments - $12.5MM refinance loan request",
    "Jane,\n\nWells Fargo can offer $12,000,000 at 65% LTV, 5.85% fixed, 60 month term, 1.0% origination fee.\n"
    "Security: first mortgage lien and assignment of rents.\n"
    "Conditions: 1.25x min DSCR and a 6-month interest reserve.\n"
    "Prepayment: yield maintenance for 3 years, then 1% open.\n"
    f"Quote valid until {_md(WF_EXPIRES)}.\n\nTom Reed\ntom.reed@wellsfargo.com\n",
    message_id="<dash-wf@wellsfargo.com>",
    sender="Tom Reed <tom.reed@wellsfargo.com>",
    to="Jane Broker <jane@brokerco.com>",
    date=_date(2),
    in_reply_to="<dash-sub@brokerco.com>",
)

# Not in the thread: must still land on the same deal (property name), never a new one.
PACIFIC_QUOTE = make_eml(
    "Pacific Bank term sheet - Parkside Apartments loan",
    "Jane,\n\nPacific Bank is pleased to quote Parkside Apartments: $11,500,000, 60% LTV, 5.60% fixed, "
    "84 month term, 0.75% origination fee, non-recourse.\nSecurity: deed of trust.\n\nAnn Lee\n",
    message_id="<dash-pb@pacificbank.com>",
    sender="Ann Lee <ann.lee@pacificbank.com>",
    to="Jane Broker <jane@brokerco.com>",
    date=_date(1),
)

WF_REVISED = make_eml(
    "Re: Parkside Apartments - $12.5MM refinance loan request",
    "Jane, after credit committee Wells Fargo can tighten the rate to 5.55% fixed. Other terms unchanged.\n",
    message_id="<dash-wf2@wellsfargo.com>",
    sender="Tom Reed <tom.reed@wellsfargo.com>",
    to="Jane Broker <jane@brokerco.com>",
    date=_date(0),
    in_reply_to="<dash-wf@wellsfargo.com>",
)


def _f(field: str, value: str, source_text: str) -> dict:
    return {"field": field, "value": value, "source_text": source_text}


def submission_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "deal_submission",
        "summary": "Refinance request for Parkside Apartments sent to lenders",
        "deal_match": {"matched_deal_id": None, "confidence": "high", "reasoning": "New deal"},
        "deal_fields": [
            _f("property_name", "Parkside Apartments", "Parkside Apartments"),
            _f("property_address", "123 Main Street", "123 Main Street, Austin TX"),
            _f("city", "Austin", "Austin TX"),
            _f("property_type", "multifamily", "240-unit multifamily"),
            _f("units", "240", "240-unit"),
            _f("loan_amount_requested", "12500000", "$12.5MM refinance"),
            _f("transaction_type", "refinance", "$12.5MM refinance"),
            _f("sponsor_name", "Oakline Capital", "Sponsor is Oakline Capital"),
        ],
        "quotes": [],
    }


def _quote(lender: str, lender_src: str, contact: str, contact_email: str, fields: list[dict]) -> dict:
    return {
        "lender_name": {"value": lender, "source_text": lender_src},
        "lender_contact_name": {"value": contact, "source_text": contact},
        "lender_contact_email": {"value": contact_email, "source_text": contact_email}
        if contact_email
        else None,
        "option_label": None,
        "fields": fields,
    }


def wf_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "lender_quote",
        "summary": "Wells Fargo quotes $12.0MM at 5.85% fixed",
        "deal_match": {"matched_deal_id": None, "confidence": "low", "reasoning": "unclear"},
        "deal_fields": [],
        "quotes": [
            _quote(
                "Wells Fargo",
                "Wells Fargo can offer",
                "Tom Reed",
                "tom.reed@wellsfargo.com",
                [
                    _f("loan_amount", "12000000", "$12,000,000"),
                    _f("ltv", "65", "65% LTV"),
                    _f("interest_rate", "5.85", "5.85% fixed"),
                    _f("rate_type", "fixed", "5.85% fixed"),
                    _f("term_months", "60", "60 month term"),
                    _f("origination_fee_pct", "1.0", "1.0% origination fee"),
                    _f(
                        "security",
                        "First mortgage lien and assignment of rents",
                        "Security: first mortgage lien",
                    ),
                    _f(
                        "conditions", "1.25x min DSCR; 6-month interest reserve", "Conditions: 1.25x min DSCR"
                    ),
                    _f(
                        "prepayment_terms",
                        "Yield maintenance 3 years, then 1% open",
                        "yield maintenance for 3",
                    ),
                    _f("expiration_date", WF_EXPIRES.isoformat(), f"Quote valid until {_md(WF_EXPIRES)}"),
                    _f("quote_status", "indicative", "Wells Fargo can offer"),
                ],
            )
        ],
    }


def pacific_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "lender_quote",
        "summary": "Pacific Bank term sheet at 5.60% fixed",
        "deal_match": {"matched_deal_id": None, "confidence": "low", "reasoning": "Not sure"},
        "deal_fields": [_f("property_name", "Parkside Apartments", "Parkside Apartments")],
        "quotes": [
            _quote(
                "Pacific Bank",
                "Pacific Bank is pleased to quote",
                "Ann Lee",
                None,
                [
                    _f("loan_amount", "11500000", "$11,500,000"),
                    _f("ltv", "60", "60% LTV"),
                    _f("interest_rate", "5.60", "5.60% fixed"),
                    _f("rate_type", "fixed", "5.60% fixed"),
                    _f("term_months", "84", "84 month term"),
                    _f("origination_fee_pct", "0.75", "0.75% origination fee"),
                    _f("recourse", "non_recourse", "non-recourse"),
                    _f("security", "Deed of trust", "Security: deed of trust"),
                    _f("quote_status", "term_sheet", "term sheet"),
                ],
            )
        ],
    }


def wf_revised_extraction(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
    return {
        "email_type": "lender_quote",
        "summary": "Wells Fargo tightens to 5.55%",
        "deal_match": {"matched_deal_id": None, "confidence": "low", "reasoning": "unclear"},
        "deal_fields": [],
        "quotes": [
            _quote(
                "Wells Fargo",
                "Wells Fargo can tighten",
                "Tom Reed",
                None,
                [_f("interest_rate", "5.55", "5.55% fixed")],
            )
        ],
    }


def _process(client, extractor, message: dict, handler) -> dict:
    extractor.handler = handler
    r = client.post(f"/api/gmail/messages/{message['id']}/process")
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _vals(quote: dict) -> dict[str, dict]:
    return {f["field"]: f for f in quote["fields"]}


def test_gmail_to_dashboard_to_quote_comparison(configured, google, extractor, client) -> None:
    google.add("g-sub", SUBMISSION)
    google.add("g-wf", WF_QUOTE)
    google.add("g-pb", PACIFIC_QUOTE)
    assert connect(client).status_code == 303
    assert client.post("/api/gmail/sync", json={}).status_code == 200
    msgs = messages_by_subject(client)

    # Submission → creates the deal; dashboard shows it out to lenders with no quotes yet.
    sub = _process(
        client, extractor, msgs["Parkside Apartments - $12.5MM refinance loan request"], submission_extraction
    )
    deal_id = sub["deal"]["id"]
    assert sub["deal"]["created"] is True
    deal = client.get(f"/api/deals/{deal_id}").json()
    assert deal["status"]["code"] == "marketing"
    assert {lender["contact_email"] for lender in deal["lenders"]} == {
        "tom.reed@wellsfargo.com",
        "ann.lee@pacificbank.com",
        "raj@harborlife.com",
    }  # the broker's cc'd colleague is not a lender
    assert {d["name"]: d["category"] for d in deal["documents"]} == {
        "Parkside_OM.pdf": "Offering memorandum",
        "Parkside Rent Roll.xlsx": "Rent roll",
    }
    [meeting] = [a for a in deal["activities"] if a["kind"] == "meeting"]
    assert meeting["scheduled_for"] == SITE_VISIT.isoformat()
    assert meeting["source"]["source_text"].startswith("The site visit is scheduled")
    [request] = [a for a in deal["pending_actions"] if a["kind"] == "request"]
    assert request["due_date"] == TERMS_DUE.isoformat() and request["overdue"] is False
    assert request["responsible"] == "Tom Reed, Ann Lee, Raj Patel"
    assert request["source"]["email_id"] == sub["email"]["id"]

    # Wells Fargo replies in the thread → quote persisted against the deal, visible immediately.
    wf = _process(
        client, extractor, msgs["Re: Parkside Apartments - $12.5MM refinance loan request"], wf_extraction
    )
    assert wf["deal"]["id"] == deal_id and wf["deal"]["match_method"] == "email_thread"
    deal = client.get(f"/api/deals/{deal_id}").json()
    [q] = deal["quotes"]
    v = _vals(q)
    assert v["security"]["value"] == "First mortgage lien and assignment of rents"
    assert v["security"]["source_text"] == "Security: first mortgage lien"
    assert v["security"]["source_email_id"] == wf["email"]["id"]
    assert v["conditions"]["value"].startswith("1.25x min DSCR")
    assert q["quote_date"] and q["source"]["email_id"] == wf["email"]["id"]
    assert q["validity"] == "valid" and q["days_to_expiry"] == 20
    assert deal["status"]["code"] == "quotes_received"

    # Pacific Bank, outside the thread, with no confident AI match → same deal via property name.
    pb = _process(
        client, extractor, msgs["Pacific Bank term sheet - Parkside Apartments loan"], pacific_extraction
    )
    assert pb["deal"]["id"] == deal_id and pb["deal"]["created"] is False
    deal = client.get(f"/api/deals/{deal_id}").json()
    assert [q["lender_name"] for q in deal["quotes"]] == ["Pacific Bank", "Wells Fargo"]
    assert deal["status"]["code"] == "term_sheet"
    lenders = {lender["name"]: lender["status"] for lender in deal["lenders"]}
    assert lenders == {
        "Pacific Bank": "term_sheet",
        "Wells Fargo": "quoted",
        "Harborlife": "awaiting_response",
    }
    # Two lenders replied; the request is still open for the one that has not.
    [request] = [a for a in deal["pending_actions"] if a["kind"] == "request"]
    assert request["responsible"] == "Raj Patel"
    [deadline] = [a for a in deal["pending_actions"] if a["kind"] == "deadline"]
    assert deadline["due_date"] == WF_EXPIRES.isoformat() and "Wells Fargo" in deadline["title"]
    titles = [a["title"] for a in deal["activities"]]
    assert "Term sheet received from Pacific Bank" in titles and "Quote received from Wells Fargo" in titles

    # Re-processing never duplicates.
    again = client.post(
        f"/api/gmail/messages/{msgs['Pacific Bank term sheet - Parkside Apartments loan']['id']}/process"
    )
    assert again.json()["result"]["duplicate"] is True

    # A revised quote updates the comparison in place (same quote, new rate, new source).
    google.add("g-wf2", WF_REVISED)
    client.post("/api/gmail/sync", json={})
    revised_msg = next(
        m for m in client.get("/api/gmail/messages").json()["messages"] if m["gmail_id"] == "g-wf2"
    )
    revised = _process(client, extractor, revised_msg, wf_revised_extraction)
    assert revised["deal"]["id"] == deal_id
    deal = client.get(f"/api/deals/{deal_id}").json()
    assert len(deal["quotes"]) == 2
    wf_quote = _vals(next(q for q in deal["quotes"] if q["lender_name"] == "Wells Fargo"))
    assert wf_quote["interest_rate"]["value"] == "5.55"
    assert wf_quote["interest_rate"]["source_email_id"] == revised["email"]["id"]
    assert wf_quote["loan_amount"]["value"] == "12000000"  # unchanged terms keep their original source

    # Dashboard list: one transaction, with status and borrower.
    [item] = client.get("/api/deals").json()
    assert item | {"id": None, "updated_at": None, "last_activity_at": None} == item | {
        "id": None,
        "updated_at": None,
        "last_activity_at": None,
        "deal_name": "Parkside Apartments",
        "quote_count": 2,
        "lender_count": 2,
        "status": "term_sheet",
        "status_label": "Term sheet",
        "sponsor_name": "Oakline Capital",
        "lowest_fixed_rate": "5.55",
        "lowest_fixed_rate_lender": "Wells Fargo",
    }


# ---------------------------------------------------------------- duplicate deals under concurrency


class _BarrierExtractor:
    """Both extractions wait for each other, so both load candidates before either deal is saved."""

    model = "fake"

    def __init__(self, handler) -> None:
        self.handler = handler
        self.waiting = 0
        self.both = asyncio.Event()

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        self.waiting += 1
        if self.waiting == 2:
            self.both.set()
        await asyncio.wait_for(self.both.wait(), 5)
        return EmailExtraction.model_validate(self.handler(email, candidates))


def test_concurrent_emails_about_a_new_deal_create_one_deal(clean_db) -> None:
    first = make_eml(
        "Harbor Point Plaza financing",
        "Seeking financing for Harbor Point Plaza.\n",
        message_id="<hp-1@a.com>",
        date=_date(1),
    )
    second = make_eml(
        "Harbor Point Plaza - lender intro",
        "Introducing Harbor Point Plaza to you.\n",
        message_id="<hp-2@b.com>",
        sender="Someone Else <x@other.com>",
        date=_date(0),
    )

    def handler(email: ParsedEmail, candidates: list[DealCandidate]) -> dict:
        assert candidates == []  # neither email sees the other's deal before extraction
        return {
            "email_type": "deal_submission",
            "summary": "Harbor Point Plaza",
            "deal_match": {"matched_deal_id": None, "confidence": "high", "reasoning": "new"},
            "deal_fields": [_f("property_name", "Harbor Point Plaza", "Harbor Point Plaza")],
            "quotes": [],
        }

    async def run() -> list:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        extractor = _BarrierExtractor(handler)

        async def ingest(raw: bytes):
            async with AsyncSession(engine, expire_on_commit=False) as db:
                return await ingest_email(db, raw, None, extractor)

        results = await asyncio.gather(ingest(first), ingest(second))
        async with AsyncSession(engine) as db:
            count = (await db.execute(select(func.count()).select_from(Deal))).scalar_one()
        await engine.dispose()
        return [results, count]

    (a, b), count = asyncio.run(run())
    assert count == 1
    assert a.deal.id == b.deal.id
    assert sorted([a.deal.created, b.deal.created]) == [False, True]


def test_parse_due_date() -> None:
    sent = date(2026, 10, 7)  # a Wednesday
    assert parse_due_date("Please send terms by Oct 15.", sent) == date(2026, 10, 15)
    assert parse_due_date("Need the rent roll by January 5", sent) == date(2027, 1, 5)
    assert parse_due_date("Let me know by Friday", sent) == date(2026, 10, 9)
    assert parse_due_date("Deadline is 10/20/2026", sent) == date(2026, 10, 20)
    assert parse_due_date("Please follow up tomorrow", sent) == date(2026, 10, 8)
    assert parse_due_date("Please send the T12", sent) is None
