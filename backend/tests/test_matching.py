from app.extraction.matching import DealCandidate, normalize_address, resolve_deal
from app.extraction.schemas import DealMatch, ValidatedExtraction, ValidatedField

PARKSIDE = DealCandidate(
    id="d1",
    deal_name="Parkside Refi",
    property_name="Parkside Apartments",
    property_address="123 Main Street",
    city="Austin",
    state="TX",
)
HARBOR = DealCandidate(
    id="d2",
    deal_name="Harbor Point",
    property_name="Harbor Point Plaza",
    property_address="9 Ocean Ave",
    city="Miami",
    state="FL",
)
CANDIDATES = [PARKSIDE, HARBOR]


def extraction(
    fields: dict[str, str] | None = None, match: DealMatch | None = None, email_type="deal_update"
):
    return ValidatedExtraction(
        email_type=email_type,
        summary="",
        deal_match=match or DealMatch(matched_deal_id=None, confidence="low", reasoning=""),
        deal_fields=[
            ValidatedField(field=k, value=v, raw_value=v, source_text=v) for k, v in (fields or {}).items()
        ],
        quotes=[],
        issues=[],
    )


def test_address_normalization() -> None:
    assert normalize_address("123 Main Street,") == normalize_address("123 main st")
    assert normalize_address("9 Ocean Avenue Suite 200") == "9 ocean ave ste 200"


def test_thread_match_wins_over_everything() -> None:
    ex = extraction(
        {"property_address": "9 Ocean Ave"}, DealMatch(matched_deal_id="d2", confidence="high", reasoning="")
    )
    r = resolve_deal(ex, CANDIDATES, thread_deal_id="d1")
    assert (r.deal_id, r.method) == ("d1", "email_thread")


def test_exact_address_match() -> None:
    r = resolve_deal(extraction({"property_address": "123 Main St.", "city": "Austin"}), CANDIDATES)
    assert (r.deal_id, r.method) == ("d1", "address")


def test_address_in_different_city_does_not_match() -> None:
    r = resolve_deal(extraction({"property_address": "123 Main Street", "city": "Denver"}), CANDIDATES)
    assert (r.deal_id, r.method) == (None, "new")


def test_address_beats_claude_when_they_disagree() -> None:
    ex = extraction(
        {"property_address": "9 Ocean Avenue"},
        DealMatch(matched_deal_id="d1", confidence="high", reasoning=""),
    )
    assert resolve_deal(ex, CANDIDATES).deal_id == "d2"


def test_claude_match_accepted_at_high_or_medium_confidence() -> None:
    for confidence in ("high", "medium"):
        ex = extraction(
            match=DealMatch(matched_deal_id="d2", confidence=confidence, reasoning="same sponsor")
        )
        r = resolve_deal(ex, CANDIDATES)
        assert (r.deal_id, r.method, r.reason) == ("d2", "claude", "same sponsor")


def test_claude_low_confidence_is_not_trusted() -> None:
    ex = extraction(match=DealMatch(matched_deal_id="d2", confidence="low", reasoning=""))
    assert resolve_deal(ex, CANDIDATES).method == "new"


def test_claude_match_must_be_a_candidate() -> None:
    ex = extraction(match=DealMatch(matched_deal_id="deleted", confidence="high", reasoning=""))
    assert resolve_deal(ex, CANDIDATES).method == "new"


def test_property_name_fallback() -> None:
    r = resolve_deal(extraction({"property_name": "harbor point PLAZA"}), CANDIDATES)
    assert (r.deal_id, r.method) == ("d2", "property_name")


def test_ambiguous_property_name_creates_new() -> None:
    dup = DealCandidate(id="d3", deal_name="Other", property_name="Harbor Point Plaza")
    assert (
        resolve_deal(extraction({"property_name": "Harbor Point Plaza"}), [*CANDIDATES, dup]).method == "new"
    )


def test_non_deal_email_gets_no_deal() -> None:
    r = resolve_deal(extraction(email_type="other"), CANDIDATES)
    assert (r.deal_id, r.method) == (None, "none")
