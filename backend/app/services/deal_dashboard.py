"""Transaction view of a deal: status, lenders approached, documents, activities and pending actions.

Like `deal_views`, everything is derived from stored emails and validated values; nothing is invented. Each item
carries the email (and, where there is one, the verbatim sentence) it was derived from.
"""

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from app.api.schemas import (
    DealActivity,
    DealDetail,
    DealDocument,
    DealStatus,
    LenderApproach,
    PendingAction,
    SourceRef,
)
from app.models import Email
from app.services.copilot import action_sentences
from app.services.deal_views import HISTORICAL_AFTER, fmt, source_ref
from app.services.email_ingestion import _CLOSING_RE, _QUOTED_LINE_RE, _QUOTED_REPLY_RE
from app.services.email_parser import EmailParseError, parse_email

# Free-mail domains: recipients there are matched by exact address, never by domain.
_GENERIC_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"}

_MEETING_RE = re.compile(
    r"\b(call|meeting|site\s+(?:visit|tour|inspection)|property\s+tour|tour|zoom|teams\s+call|walk-?through|"
    r"conference\s+call|lunch|dinner)\b",
    re.IGNORECASE,
)

_MONTHS = {m: i for i, m in enumerate(("jan feb mar apr may jun jul aug sep oct nov dec").split(), start=1)}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DATE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b"
    r"|\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"
    r"|\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\b(tomorrow|end\s+of\s+(?:the\s+)?week|eow)\b",
    re.IGNORECASE,
)

_DOC_CATEGORIES = (
    ("Term sheet", r"term[\s_-]*sheet|loi|proposal|quote"),
    ("Rent roll", r"rent[\s_-]*roll"),
    ("Operating statement", r"\bt-?12\b|t12|operating|p&l|financials?"),
    ("Offering memorandum", r"\bom\b|offering|memorandum|package|teaser"),
    ("Appraisal", r"apprais"),
    ("Environmental / PCA", r"phase|environmental|esa|pca|property[\s_-]*condition"),
    ("Sponsor / PFS", r"pfs|sreo|resume|bio|financial[\s_-]*statement"),
    ("Closing", r"closing|settlement|loan[\s_-]*agreement|commitment"),
)

_QUOTE_STAGE = {"application": 3, "term_sheet": 2}


# ---------------------------------------------------------------- helpers


def new_text(body: str) -> str:
    """The newest part of an email: quoted replies removed."""
    cleaned = _QUOTED_LINE_RE.sub("", _QUOTED_REPLY_RE.sub("", body)).strip()
    return cleaned or body


def _sentences(body: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", new_text(body))
    return [p.strip() for p in parts if 10 <= len(p.strip()) <= 300]


def email_ref(e: Email, source_text: str | None = None, label: str | None = None) -> SourceRef:
    return SourceRef(
        email_id=e.id,
        email_subject=e.subject,
        email_sender=e.sender_email,
        email_sent_at=e.sent_at,
        source_text=source_text,
        label=label,
    )


def _when(e: Email) -> datetime:
    return e.sent_at or e.created_at


def _domain(address: str | None) -> str:
    return address.rsplit("@", 1)[-1].lower() if address and "@" in address else ""


def _recipients(e: Email, kinds: Iterable[str] = ("to", "cc")) -> list[dict]:
    return [r for k in kinds for r in (e.recipients or {}).get(k, []) if r.get("email")]


def _person(r: dict) -> str:
    return r.get("name") or r["email"]


def parse_due_date(sentence: str, sent: date) -> date | None:
    """First date stated in a sentence, resolved against the email's sent date (no year → next occurrence)."""
    m = _DATE_RE.search(sentence)
    if m is None:
        return None
    try:
        if m.group(1):
            year = int(m.group(3)) if m.group(3) else sent.year
            d = date(year, _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
            if not m.group(3) and d < sent - timedelta(days=30):
                d = d.replace(year=year + 1)
            return d
        if m.group(4):
            year = int(m.group(6)) if m.group(6) else sent.year
            year += 2000 if year < 100 else 0
            d = date(year, int(m.group(4)), int(m.group(5)))
            if not m.group(6) and d < sent - timedelta(days=30):
                d = d.replace(year=year + 1)
            return d
    except ValueError:
        return None
    if m.group(7):
        ahead = (_WEEKDAYS.index(m.group(7).lower()) - sent.weekday()) % 7 or 7
        return sent + timedelta(days=ahead)
    word = m.group(8).lower()
    if word == "tomorrow":
        return sent + timedelta(days=1)
    return sent + timedelta(days=(4 - sent.weekday()) % 7)  # end of week → Friday


def _closing_sentence(emails: list[Email]) -> tuple[Email, str] | None:
    for e in sorted(emails, key=_when, reverse=True):
        if e.status != "processed":
            continue
        for s in _sentences(e.body_text):
            if _CLOSING_RE.search(s):
                return e, s
    return None


def _quote_responses(e: Email) -> list[tuple[str, str | None]]:
    """(lender name, quote status) for each quote extracted from an email (from its stored result)."""
    out = []
    for q in (e.result or {}).get("quotes", []):
        status = next((f["value"] for f in q.get("fields", []) if f["field"] == "quote_status"), None)
        out.append((q["lender"]["name"], status))
    return out


# ---------------------------------------------------------------- lenders


def _quoting_addresses(detail: DealDetail, emails: list[Email]) -> tuple[set[str], set[str]]:
    """Addresses and (non-generic) domains of lenders that have responded on this deal."""
    addresses = {q.lender_contact_email.lower() for q in detail.quotes if q.lender_contact_email}
    addresses |= {e.sender_email.lower() for e in emails if _quote_responses(e) and e.sender_email}
    domains = {_domain(a) for a in addresses} - _GENERIC_DOMAINS - {""}
    return addresses, domains


def awaiting_lenders(
    emails: list[Email], quoting_addresses: set[str], quoting_domains: set[str]
) -> dict[str, tuple[dict, Email, Email]]:
    """Recipients of deal submissions that have not responded: address → (recipient, first email, last email).

    The submitter's own colleagues (same domain) and any responding lender's address/domain are excluded.
    """
    out: dict[str, tuple[dict, Email, Email]] = {}
    for e in sorted(emails, key=_when):
        if e.email_type != "deal_submission":
            continue
        own = _domain(e.sender_email)
        for r in _recipients(e):
            addr = r["email"].lower()
            dom = _domain(addr)
            if addr == (e.sender_email or "").lower() or (dom == own and dom not in _GENERIC_DOMAINS):
                continue
            if addr in quoting_addresses or dom in quoting_domains:
                continue
            first = out[addr][1] if addr in out else e
            out[addr] = (r, first, e)
    return out


def _org_name(r: dict) -> str:
    dom = _domain(r["email"])
    if dom and dom not in _GENERIC_DOMAINS:
        return dom.split(".")[0].replace("-", " ").title()
    return _person(r)


def lenders_approached(detail: DealDetail, emails: list[Email]) -> list[LenderApproach]:
    by_lender: dict = {}
    for q in detail.quotes:
        by_lender.setdefault(q.lender_id, []).append(q)
    out = []
    for lender_id, quotes in by_lender.items():
        statuses = [next((f.value for f in q.fields if f.field == "quote_status"), None) for q in quotes]
        if all(s in ("declined", "withdrawn") for s in statuses):
            status = "declined"
        else:
            best = max(statuses, key=lambda s: _QUOTE_STAGE.get(s or "", 0))
            status = best if best in _QUOTE_STAGE else "quoted"
        dates = [d for q in quotes for d in (q.quote_date, q.last_updated_at) if d]
        first = quotes[0]
        sourced = [q for q in quotes if q.source]
        out.append(
            LenderApproach(
                name=first.lender_name,
                lender_id=lender_id,
                contact_name=first.lender_contact_name,
                contact_email=first.lender_contact_email,
                status=status,
                quote_count=len(quotes),
                first_contact_at=min(dates, default=None),
                last_contact_at=max(dates, default=None),
                source=min(sourced, key=lambda q: q.quote_date or q.updated_at).source if sourced else None,
            )
        )

    addresses, domains = _quoting_addresses(detail, emails)
    for r, first, last in awaiting_lenders(emails, addresses, domains).values():
        out.append(
            LenderApproach(
                name=_org_name(r),
                contact_name=r.get("name"),
                contact_email=r["email"],
                status="awaiting_response",
                quote_count=0,
                first_contact_at=first.sent_at,
                last_contact_at=last.sent_at,
                source=email_ref(first, label="Deal sent to lender"),
            )
        )
    order = {"term_sheet": 0, "application": 0, "quoted": 1, "awaiting_response": 2, "declined": 3}
    return sorted(out, key=lambda lender: (order[lender.status], lender.name.lower()))


# ---------------------------------------------------------------- status


def deal_status(
    quote_statuses: list[str | None], emails: list[Email], awaiting: int, now: datetime
) -> DealStatus:
    closing = _closing_sentence(emails)
    if closing:
        e, sentence = closing
        return DealStatus(
            code="closed", label="Closed", reason=sentence, sources=[email_ref(e, sentence, "Closing")]
        )
    last = max((e.sent_at for e in emails if e.sent_at), default=None)
    if last is not None and now - last > HISTORICAL_AFTER:
        return DealStatus(code="inactive", label="Inactive", reason="No email activity in the last 180 days.")
    live = [s for s in quote_statuses if s not in ("declined", "withdrawn")]
    if "application" in live:
        return DealStatus(code="application", label="Application", reason="A lender quote is at application.")
    if "term_sheet" in live:
        return DealStatus(code="term_sheet", label="Term sheet", reason="A lender has issued a term sheet.")
    if live:
        return DealStatus(
            code="quotes_received",
            label="Quotes received",
            reason=f"{len(live)} active quote(s)"
            + (f", {len(quote_statuses) - len(live)} declined" if len(live) < len(quote_statuses) else "")
            + (f", {awaiting} lender(s) yet to respond" if awaiting else "")
            + ".",
        )
    if quote_statuses and not awaiting:
        return DealStatus(
            code="all_declined", label="All lenders declined", reason="Every quoting lender declined."
        )
    if awaiting or quote_statuses:
        return DealStatus(
            code="marketing",
            label="Out to lenders",
            reason=f"Sent to {awaiting} lender(s); no quotes yet."
            if not quote_statuses
            else f"All responses so far declined; {awaiting} lender(s) yet to respond.",
        )
    return DealStatus(code="intake", label="Intake", reason="Deal received; not yet sent to lenders.")


# ---------------------------------------------------------------- documents


def _category(name: str) -> str:
    lowered = re.sub(r"[_.\-]+", " ", name.lower())  # "Parkside_OM.pdf" → "parkside om pdf"
    return next((label for label, pattern in _DOC_CATEGORIES if re.search(pattern, lowered)), "Other")


def attachment_names(e: Email) -> list[str]:
    try:
        return parse_email(e.raw_source.encode("utf-8")).attachment_names
    except EmailParseError:
        return []


def documents(emails: list[Email], attachments: dict) -> list[DealDocument]:
    return [
        DealDocument(
            name=name,
            category=_category(name),
            email_id=e.id,
            email_subject=e.subject,
            email_sender=e.sender_email,
            email_sent_at=e.sent_at,
        )
        for e in sorted(emails, key=_when, reverse=True)
        for name in attachments.get(e.id, [])
    ]


# ---------------------------------------------------------------- activities


def activities(emails: list[Email]) -> list[DealActivity]:
    out: list[DealActivity] = []
    for e in sorted(emails, key=_when, reverse=True):
        if e.status != "processed":
            continue
        sender = e.sender_name or e.sender_email or "Unknown sender"
        responses = _quote_responses(e)
        if responses:
            for lender, status in responses:
                declined = status in ("declined", "withdrawn")
                stage = "Term sheet" if status == "term_sheet" else "Quote"
                out.append(
                    DealActivity(
                        kind="decline" if declined else "quote",
                        title=f"{lender} declined" if declined else f"{stage} received from {lender}",
                        text=e.summary,
                        at=e.sent_at,
                        source=email_ref(e),
                    )
                )
        elif e.email_type == "deal_submission":
            n = len(_recipients(e))
            out.append(
                DealActivity(
                    kind="submission",
                    title=f"Deal sent by {sender}" + (f" to {n} recipient(s)" if n else ""),
                    text=e.summary,
                    at=e.sent_at,
                    source=email_ref(e),
                )
            )
        else:
            out.append(
                DealActivity(
                    kind="update" if e.email_type == "deal_update" else "email",
                    title=f"{'Deal update' if e.email_type == 'deal_update' else 'Email'} from {sender}",
                    text=e.summary,
                    at=e.sent_at,
                    source=email_ref(e),
                )
            )
        for s in _sentences(e.body_text):
            if _MEETING_RE.search(s):
                out.append(
                    DealActivity(
                        kind="meeting",
                        title=f"Meeting / call mentioned by {sender}",
                        text=s,
                        at=e.sent_at,
                        scheduled_for=parse_due_date(s, _when(e).date()),
                        source=email_ref(e, s, "Meeting"),
                    )
                )
    return out


# ---------------------------------------------------------------- pending actions


def _responsible_for_request(e: Email, sentence: str, replied: set[str]) -> str | None:
    """Who must act: the sender for "I/we will ..." commitments, otherwise the To recipients who have not
    replied since. Returns "" when every addressee has replied (the request is treated as answered)."""
    if re.match(r"\s*(?:i|we)(?:'ll|\s+will|\s+would|\s+need)\b", sentence, re.IGNORECASE):
        return e.sender_name or e.sender_email
    to = _recipients(e, ("to",))
    if not to:
        return None
    open_ = [_person(r) for r in to if r["email"].lower() not in replied]
    if len(open_) > 3:
        return f"{', '.join(open_[:3])} +{len(open_) - 3}"
    return ", ".join(open_)


def pending_actions(
    detail: DealDetail, emails: list[Email], today: date, broker: str | None
) -> list[PendingAction]:
    """Upcoming deadlines (quote expirations, target closing) and open requests from emails.

    A request is dropped once everyone it was addressed to has sent a later email on the deal (treated as
    answered); until then the addressees who have not replied are responsible. Items are sorted by due date (overdue first), then undated requests, newest first.
    """
    team = broker or "Deal team"
    items: list[PendingAction] = []
    for q in detail.quotes:
        exp = next((f for f in q.fields if f.field == "expiration_date"), None)
        status = next((f.value for f in q.fields if f.field == "quote_status"), None)
        if exp is None or status in ("declined", "withdrawn"):
            continue
        due = date.fromisoformat(exp.value)
        if due < today - timedelta(days=14):
            continue
        lender = f"{q.lender_name} ({q.option_label})" if q.option_label else q.lender_name
        items.append(
            PendingAction(
                kind="deadline",
                title=f"Respond to {lender} before the quote expires",
                text=f"Quote expires {fmt(exp)}.",
                responsible=team,
                due_date=due,
                overdue=due < today,
                source=(source_ref(exp) or [None])[0],
            )
        )
    closing = next((f for f in detail.fields if f.field == "target_closing_date"), None)
    if closing:
        due = date.fromisoformat(closing.value)
        if due >= today - timedelta(days=14):
            items.append(
                PendingAction(
                    kind="deadline",
                    title="Close the financing",
                    text=f"Target closing {fmt(closing)}.",
                    responsible=team,
                    due_date=due,
                    overdue=due < today,
                    source=(source_ref(closing) or [None])[0],
                )
            )

    ordered = sorted(emails, key=_when)
    for i, e in enumerate(ordered):
        if e.status != "processed":
            continue
        later_senders = {(x.sender_email or "").lower() for x in ordered[i + 1 :]}
        for sentence in action_sentences(e.body_text):
            responsible = _responsible_for_request(e, sentence, later_senders)
            if responsible == "":
                continue  # every addressee has replied since
            due = parse_due_date(sentence, _when(e).date())
            if due is not None and due < today - timedelta(days=30):
                continue
            items.append(
                PendingAction(
                    kind="request",
                    title=f"Request from {e.sender_name or e.sender_email}",
                    text=sentence,
                    responsible=responsible,
                    due_date=due,
                    overdue=due is not None and due < today,
                    source=email_ref(e, sentence, "Request"),
                )
            )

    dated = sorted((a for a in items if a.due_date), key=lambda a: a.due_date)
    undated = sorted(
        (a for a in items if not a.due_date),
        key=lambda a: a.source.email_sent_at.timestamp() if a.source and a.source.email_sent_at else 0,
        reverse=True,
    )
    return (dated + undated)[:15]


# ---------------------------------------------------------------- entry points


def build_dashboard(detail: DealDetail, emails: list[Email], now: datetime | None = None) -> None:
    """Fill the transaction sections of a deal detail in place."""
    now = now or datetime.now(UTC)
    attachments = {e.id: attachment_names(e) for e in emails}
    for de in detail.emails:
        e = next(x for x in emails if x.id == de.id)
        de.sender_name = e.sender_name
        de.to = [_person(r) for r in _recipients(e, ("to",))]
        de.attachment_names = attachments.get(e.id, [])

    detail.lenders = lenders_approached(detail, emails)
    awaiting = sum(1 for lender in detail.lenders if lender.status == "awaiting_response")
    statuses = [next((f.value for f in q.fields if f.field == "quote_status"), None) for q in detail.quotes]
    detail.status = deal_status(statuses, emails, awaiting, now)
    detail.documents = documents(emails, attachments)
    detail.activities = activities(emails)
    broker = next((f.value for f in detail.fields if f.field == "broker_name"), None)
    detail.pending_actions = pending_actions(detail, emails, now.date(), broker)


def list_status(deal_quotes, emails: list[Email], now: datetime) -> DealStatus:
    """Status for the deal list, from ORM quotes (with lenders loaded) and emails."""
    addresses = {q.lender.contact_email.lower() for q in deal_quotes if q.lender.contact_email}
    addresses |= {e.sender_email.lower() for e in emails if _quote_responses(e) and e.sender_email}
    domains = {_domain(a) for a in addresses} - _GENERIC_DOMAINS - {""}
    awaiting = len(awaiting_lenders(emails, addresses, domains))
    return deal_status([q.quote_status for q in deal_quotes], emails, awaiting, now)
