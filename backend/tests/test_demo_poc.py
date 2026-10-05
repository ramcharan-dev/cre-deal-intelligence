"""POC end-to-end: demo provider → ingestion → deals/quotes → comparison → sources → summary → search/Copilot.

Uses the deterministic DemoExtractor and the bundled demo emails; no model or external API is called.
"""

import asyncio
from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.extraction import providers
from app.extraction.claude import ExtractionError
from app.extraction.demo import DemoExtractor, load_fixtures
from app.extraction.validation import normalize_for_match, validate_extraction
from app.main import app
from app.scripts.seed_demo import demo_emails
from app.services import copilot
from app.services.email_parser import parse_email
from tests.test_email_ingestion import SUBMISSION

RIVERBEND = "The Lofts at Riverbend"


# ---------------------------------------------------------------- provider


def test_demo_provider_is_selected_by_configuration(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_provider", "demo")
    assert isinstance(providers.get_extractor(), DemoExtractor)
    assert isinstance(providers.create_extractor(" Demo "), DemoExtractor)


def test_every_demo_email_has_a_fixture_that_validates_cleanly() -> None:
    fixtures = load_fixtures()
    paths = demo_emails()
    assert len(paths) == len(fixtures) == 15
    for path in paths:
        parsed = parse_email(path.read_bytes())
        validated = validate_extraction(fixtures[parsed.message_id], parsed.as_prompt_text(), set())
        assert validated.issues == [], path.name


def test_demo_extractor_rejects_unknown_emails() -> None:
    with pytest.raises(ExtractionError) as exc:
        asyncio.run(DemoExtractor(strict=True).extract(parse_email(SUBMISSION), []))
    assert exc.value.code == "not_configured"


# ---------------------------------------------------------------- seeded app


@pytest.fixture
def client(clean_db) -> Iterator[TestClient]:
    app.dependency_overrides[providers.get_extractor] = lambda: DemoExtractor(strict=True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()



@pytest.fixture
def seeded(client) -> dict[str, dict]:
    """Upload every demo email through the API, oldest first. Returns results by filename."""
    results = {}
    for path in demo_emails():
        r = client.post("/api/emails", files={"file": (path.name, path.read_bytes(), "message/rfc822")})
        assert r.status_code == 200, (path.name, r.text)
        results[path.name] = r.json()
    return results


def deal_by_name(client: TestClient, name: str) -> dict:
    item = next(d for d in client.get("/api/deals").json() if d["deal_name"] == name)
    return client.get(f"/api/deals/{item['id']}").json()


def ask(client: TestClient, question: str, deal_id: str | None = None) -> dict:
    r = client.post("/api/copilot", json={"question": question, "deal_id": deal_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_unknown_email_with_demo_provider_is_a_clear_error(client) -> None:
    r = client.post("/api/emails", files={"file": ("e.eml", SUBMISSION, "message/rfc822")})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "not_configured"


def test_emails_are_identified_and_matched_to_the_right_deals(seeded) -> None:
    submission = seeded["01_deal_submission.eml"]
    assert submission["email_type"] == "deal_submission"
    assert submission["deal"]["created"] is True and submission["deal"]["deal_name"] == RIVERBEND
    riverbend_id = submission["deal"]["id"]

    expected = {
        "02_lender_quote_reply.eml": "email_thread",
        "03_quote_summary.eml": "address",
        "04_beacon_term_sheet.eml": "property_name",
    }
    for name, method in expected.items():
        assert seeded[name]["deal"]["id"] == riverbend_id, name
        assert seeded[name]["deal"]["match_method"] == method, name

    assert seeded["07_mesa_ironwood_quote.eml"]["deal"]["match_method"] == "property_name"
    newsletter = seeded["15_market_newsletter.eml"]
    assert newsletter["email_type"] == "other" and newsletter["deal"] is None
    assert all(r["issues"] == [] for r in seeded.values())


def test_dashboard_lists_deals_with_best_terms(seeded, client) -> None:
    deals = {d["deal_name"]: d for d in client.get("/api/deals").json()}
    assert set(deals) == {
        RIVERBEND,
        "Mesa Logistics Center",
        "Coastal Commons",
        "Cedar Grove Apartments",
        "Westlake Medical Office Building",
    }
    riverbend = deals[RIVERBEND]
    assert (riverbend["quote_count"], riverbend["lender_count"], riverbend["email_count"]) == (4, 4, 4)
    assert riverbend["max_loan_amount"] == "50000000"
    assert (riverbend["lowest_fixed_rate"], riverbend["lowest_fixed_rate_lender"]) == (
        "5.12",
        "Beacon Agency Lending",
    )
    assert deals["Cedar Grove Apartments"]["is_historical"] is True
    assert deals["Westlake Medical Office Building"]["is_historical"] is True
    assert sum(d["quote_count"] for d in deals.values()) == 9


def test_multiple_quotes_are_stored_with_newest_values_and_sources(seeded, client) -> None:
    deal = deal_by_name(client, RIVERBEND)
    quotes = {q["lender_name"]: {f["field"]: f for f in q["fields"]} for q in deal["quotes"]}
    assert set(quotes) == {
        "Beacon Agency Lending",
        "Granite Federal Bank",
        "Northmark Life Insurance Company",
        "Summit Bridge Capital",
    }

    beacon = quotes["Beacon Agency Lending"]
    rate = beacon["interest_rate"]
    assert rate["value"] == "5.12"  # the term sheet supersedes the 5.18% round-one indication
    assert rate["source_text"] == "10-year fixed at 5.12%"
    assert rate["source_email_subject"] == "Beacon term sheet - The Lofts at Riverbend"
    assert rate["source_email_sent_at"].startswith("2026-10-01")
    assert rate["source_email_sender"] == "lchen@beaconagency.com"
    assert beacon["interest_only_months"]["value"] == "60"
    assert beacon["quote_status"]["value"] == "term_sheet"

    northmark = quotes["Northmark Life Insurance Company"]
    assert northmark["spread_bps"]["value"] == "145"
    assert northmark["spread_bps"]["source_text"] == "UST + 145 bps (down from 155)"
    assert northmark["ltv"]["value"] == "61.5"  # kept from the original quote
    assert quotes["Granite Federal Bank"]["quote_status"]["value"] == "declined"
    assert quotes["Summit Bridge Capital"]["rate_type"]["value"] == "floating"


def test_every_source_text_is_found_in_its_source_email(seeded, client) -> None:
    deal = deal_by_name(client, RIVERBEND)
    values = deal["fields"] + [f for q in deal["quotes"] for f in q["fields"]]
    assert values and all(v["source_email_id"] for v in values)
    texts = {}
    for v in values:
        email_id = v["source_email_id"]
        if email_id not in texts:
            r = client.get(f"/api/emails/{email_id}/source")
            assert r.status_code == 200
            texts[email_id] = normalize_for_match(r.json()["text"])
        assert normalize_for_match(v["source_text"]) in texts[email_id], v["field"]

    term_sheet = seeded["04_beacon_term_sheet.eml"]["email"]["id"]
    source = client.get(f"/api/emails/{term_sheet}/source").json()
    assert source["attachment_names"] == ["Beacon_Riverbend_Term_Sheet.txt"]
    assert source["deal_id"] == deal["id"]
    assert client.get("/api/emails/00000000-0000-0000-0000-000000000000/source").status_code == 404


def test_deal_summary_is_built_from_sourced_values(seeded, client) -> None:
    summary = deal_by_name(client, RIVERBEND)["summary"]
    assert summary["headline"] == (
        "Cascade Ridge Partners is seeking a $50,000,000 refinance loan at about 64% LTV, "
        "targeting closing on Dec 15, 2026."
    )
    points = {p["text"]: p for p in summary["points"]}
    assert "The Lofts at Riverbend is a 212-unit multifamily property in Sacramento, CA." in points
    best = points["Lowest fixed rate: 5.12% from Beacon Agency Lending."]
    assert best["sources"][0]["source_text"] == "10-year fixed at 5.12%"
    assert any("Declined: Granite Federal Bank" in t for t in points)
    assert all(p["sources"] for p in summary["points"])

    closed = deal_by_name(client, "Cedar Grove Apartments")["summary"]["points"][-1]
    assert closed["text"].startswith("Latest email (May 30, 2025): Cedar Grove Apartments closed")


def test_copilot_compares_quotes(seeded, client) -> None:
    deal_id = deal_by_name(client, RIVERBEND)["id"]
    answer = ask(client, "Compare lender quotes for this deal.", deal_id)
    assert (answer["intent"], answer["mode"]) == ("compare", "structured")
    assert "lowest fixed rate 5.12% (Beacon Agency Lending)" in answer["answer"]
    assert "declined: Granite Federal Bank" in answer["answer"]
    assert len(answer["items"]) == 4 and all(i["sources"] for i in answer["items"])


def test_copilot_lowest_fixed_rate_names_the_deal_and_cites_the_source(seeded, client) -> None:
    answer = ask(client, "Which lender has the lowest fixed rate on Riverbend?")
    assert answer["deal_name"] == RIVERBEND
    assert (
        answer["answer"]
        == "The lowest fixed rate on The Lofts at Riverbend is 5.12% from Beacon Agency Lending."
    )
    first = answer["items"][0]
    assert first["title"] == "Beacon Agency Lending"
    assert first["sources"][0]["source_text"] == "10-year fixed at 5.12%"
    assert any("not directly comparable" in i["title"] for i in answer["items"])  # Summit is floating


def test_copilot_declined_and_lenders(seeded, client) -> None:
    declined = ask(client, "Which lender declined Riverbend?")
    assert declined["answer"] == "Granite Federal Bank declined The Lofts at Riverbend."
    assert "Granite Federal Bank passed" in declined["items"][0]["sources"][0]["source_text"]

    deal_id = deal_by_name(client, "Mesa Logistics Center")["id"]
    lenders = ask(client, "What lenders are associated with this deal?", deal_id)
    assert lenders["intent"] == "lenders"
    assert [i["title"] for i in lenders["items"]] == ["Ironwood Debt Partners", "Pacific Crest Bank"]


def test_copilot_pending_actions(seeded, client, monkeypatch) -> None:
    class Oct3(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 10, 3)

    monkeypatch.setattr(copilot, "date", Oct3)
    deal_id = deal_by_name(client, RIVERBEND)["id"]
    answer = ask(client, "What are the pending actions?", deal_id)
    assert answer["intent"] == "pending_actions"
    titles = [i["title"] for i in answer["items"]]
    assert titles[0] == "Beacon Agency Lending quote expires Oct 21, 2026 (18 days)"
    assert "Northmark Life Insurance Company quote expires Oct 30, 2026 (27 days)" in titles
    deposit = next(i for i in answer["items"] if "good faith deposit" in i["text"])
    assert deposit["sources"][0]["source_text"] == deposit["text"]
    assert deposit["sources"][0]["email_subject"] == "Beacon term sheet - The Lofts at Riverbend"


def test_copilot_searches_historical_deals(seeded, client) -> None:
    answer = ask(client, "When did Cedar Grove close?")
    assert (answer["intent"], answer["mode"]) == ("search", "search")
    assert answer["deal_name"] == "Cedar Grove Apartments"
    assert any(
        i["sources"] and i["sources"][0]["email_subject"] == "Closed - Cedar Grove Apartments"
        for i in answer["items"]
    )

    nothing = ask(client, "zebra unicorn")
    assert nothing["items"] == [] and nothing["answer"].startswith("No matches")


def test_search_returns_sourced_hits(seeded, client) -> None:
    hits = client.get("/api/search", params={"q": "SOFR floor"}).json()
    assert hits
    top = hits[0]
    assert top["kind"] == "field" and "SOFR floor" in top["source"]["source_text"]
    assert {h["deal_name"] for h in hits} >= {RIVERBEND, "Mesa Logistics Center"}
