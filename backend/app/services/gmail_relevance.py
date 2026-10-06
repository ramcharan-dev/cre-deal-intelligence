"""Keyword-based CRE relevance and category for synced Gmail messages.

Deterministic and free: it decides which messages are worth storing and offering for AI processing.
It does not extract anything; Claude does that when a message is processed.
"""

import re
from typing import Literal

GmailCategory = Literal["lender_quote", "deal_update", "financing", "term_sheet", "follow_up", "other"]


def _terms(*words: str) -> list[re.Pattern[str]]:
    return [re.compile(rf"(?<![a-z0-9]){w}(?![a-z0-9])") for w in words]


# (category, points per distinct match, patterns)
SIGNALS: list[tuple[GmailCategory, int, list[re.Pattern[str]]]] = [
    (
        "term_sheet",
        14,
        _terms(
            r"term ?sheets?",
            r"commitment letter",
            r"loan application",
            r"letter of intent",
            r"loi",
            r"signed terms",
        ),
    ),
    (
        "lender_quote",
        8,
        _terms(
            r"indicative( terms)?",
            r"quotes?",
            r"spread",
            r"bps",
            r"basis points",
            r"sofr",
            r"ust",
            r"treasur(y|ies)",
            r"ltv",
            r"ltc",
            r"dscr",
            r"debt yield",
            r"amortization",
            r"interest[- ]only",
            r"origination fee",
            r"exit fee",
            r"prepayment",
            r"yield maintenance",
            r"defeasance",
            r"(non-?)?recourse",
            r"rate lock",
        ),
    ),
    (
        "financing",
        9,
        _terms(
            r"refinanc(e|ing)",
            r"refi",
            r"financing",
            r"loan request",
            r"debt request",
            r"loan terms",
            r"debt placement",
            r"acquisition loan",
            r"bridge loan",
            r"construction loan",
            r"lender outreach",
            r"capital stack",
            r"mezz(anine)?",
            r"preferred equity",
            r"loan sizing",
            r"cash-out",
            r"mandate",
        ),
    ),
    (
        "deal_update",
        6,
        _terms(
            r"rent roll",
            r"t-?12",
            r"occupancy",
            r"noi",
            r"cap rate",
            r"offering memorandum",
            r"om",
            r"psa",
            r"under contract",
            r"appraisal",
            r"phase i",
            r"estoppels?",
            r"lease (renewal|extension)",
            r"anchor tenant",
            r"closing date",
            r"data room",
        ),
    ),
]

PROPERTY_TERMS = _terms(
    r"multifamily",
    r"apartments?",
    r"industrial",
    r"warehouse",
    r"retail",
    r"shopping center",
    r"office",
    r"self[- ]storage",
    r"hotel",
    r"hospitality",
    r"mixed[- ]use",
    r"units",
    r"square feet",
    r"sf",
    r"class [ab]",
)
FOLLOW_UP = _terms(
    r"following up", r"follow(ing)?[- ]up", r"circling back", r"checking in", r"any update", r"reminder"
)
NOISE = _terms(r"unsubscribe", r"newsletter", r"webinar", r"view in browser", r"% off", r"promo code")

MONEY = re.compile(r"\$\s?\d[\d,.]*\s?(mm|m|million|k)?\b")
PERCENT = re.compile(r"\d(\.\d+)?\s?%")

# Subject matches count double: subjects are short and deliberate.
_SUBJECT_WEIGHT = 2


def _points(patterns: list[re.Pattern[str]], subject: str, body: str, per_match: int) -> int:
    total = 0
    for p in patterns:
        if p.search(subject):
            total += per_match * _SUBJECT_WEIGHT
        elif p.search(body):
            total += per_match
    return total


def classify(subject: str, body: str) -> tuple[GmailCategory, int]:
    """(category, relevance 0-100)."""
    subject, body = subject.lower(), body.lower()[:20_000]
    scores = {cat: _points(patterns, subject, body, pts) for cat, pts, patterns in SIGNALS}
    property_points = min(_points(PROPERTY_TERMS, subject, body, 6), 24)
    follow_up_subject = any(p.search(subject) for p in FOLLOW_UP)
    follow_up = follow_up_subject or any(p.search(body[:600]) for p in FOLLOW_UP)
    noise = sum(1 for p in NOISE if p.search(body))
    figures = 8 if MONEY.search(subject + " " + body) and PERCENT.search(subject + " " + body) else 0

    relevance = sum(scores.values()) + property_points + figures + (10 if follow_up else 0) - 25 * noise
    relevance = max(0, min(100, relevance))

    best: GmailCategory = max(("lender_quote", "financing", "deal_update"), key=lambda c: scores[c])
    # A term sheet is named in the subject; "term sheet expected next week" in a body is not one.
    if any(p.search(subject) for p in SIGNALS[0][2]) and relevance >= 30:
        return "term_sheet", relevance
    if follow_up_subject and relevance >= 20:
        return "follow_up", relevance
    if scores[best] >= 14:
        return best, relevance
    if follow_up and relevance >= 20:
        return "follow_up", relevance
    return "other", relevance
