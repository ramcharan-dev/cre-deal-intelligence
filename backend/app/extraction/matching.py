"""Decide which deal an email belongs to.

Precedence (first hit wins):
  1. email_thread  - the email replies to / references an email already linked to a deal
  2. address       - extracted street address + city match an existing deal exactly (normalized)
  3. claude        - Claude picked a candidate deal with high or medium confidence
  4. property_name - extracted property or deal name matches an existing deal's property or deal name exactly
                     (normalized)
  5. new           - otherwise a new deal is created (only if the email carries deal content)
"""

import re
from dataclasses import dataclass
from typing import Literal

from app.extraction.schemas import ValidatedExtraction

MatchMethod = Literal["email_thread", "address", "claude", "property_name", "new", "none"]

_ADDRESS_ABBREVIATIONS = {
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "road": "rd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "parkway": "pkwy",
    "highway": "hwy",
    "suite": "ste",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}


@dataclass(frozen=True)
class DealCandidate:
    id: str
    deal_name: str
    property_name: str | None = None
    property_address: str | None = None
    city: str | None = None
    state: str | None = None
    property_type: str | None = None
    sponsor_name: str | None = None
    recent_subjects: tuple[str, ...] = ()


@dataclass(frozen=True)
class DealResolution:
    deal_id: str | None
    method: MatchMethod
    reason: str


def normalize_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def normalize_address(value: str | None) -> str:
    tokens = normalize_name(value).split()
    return " ".join(_ADDRESS_ABBREVIATIONS.get(t, t) for t in tokens)


def resolve_deal(
    extraction: ValidatedExtraction,
    candidates: list[DealCandidate],
    thread_deal_id: str | None = None,
) -> DealResolution:
    if thread_deal_id is not None:
        return DealResolution(thread_deal_id, "email_thread", "Reply to an email already linked to this deal")

    fields = {f.field: str(f.value) for f in extraction.deal_fields}

    address = normalize_address(fields.get("property_address"))
    city = normalize_name(fields.get("city"))
    if address:
        hits = [
            c
            for c in candidates
            if normalize_address(c.property_address) == address
            and (not city or not c.city or normalize_name(c.city) == city)
        ]
        if len(hits) == 1:
            return DealResolution(hits[0].id, "address", f"Property address matches '{hits[0].deal_name}'")

    match = extraction.deal_match
    if match.matched_deal_id and match.confidence in ("high", "medium"):
        if any(c.id == match.matched_deal_id for c in candidates):
            return DealResolution(match.matched_deal_id, "claude", match.reasoning)

    # Property or deal name, compared against both names of each candidate ("Riverbend Lofts" may be the deal
    # name on one email and the property name on the next).
    names = {normalize_name(fields.get(k)) for k in ("property_name", "deal_name")} - {""}
    if names:
        hits = [
            c
            for c in candidates
            if names & ({normalize_name(c.property_name), normalize_name(c.deal_name)} - {""})
        ]
        if len(hits) == 1:
            return DealResolution(
                hits[0].id, "property_name", f"Property/deal name matches '{hits[0].deal_name}'"
            )

    if not extraction.has_deal_content:
        return DealResolution(None, "none", "Email does not describe a CRE deal")
    return DealResolution(None, "new", "No existing deal matched")
