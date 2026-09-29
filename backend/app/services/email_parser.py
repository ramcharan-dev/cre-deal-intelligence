"""Parse raw RFC 822 / .eml bytes into the fields the pipeline needs (stdlib only)."""

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime


class EmailParseError(ValueError):
    pass


@dataclass(frozen=True)
class Address:
    name: str | None
    email: str

    def display(self) -> str:
        return f"{self.name} <{self.email}>" if self.name else self.email


@dataclass(frozen=True)
class ParsedEmail:
    message_id: str | None
    subject: str
    sender: Address | None
    to: list[Address]
    cc: list[Address]
    sent_at: datetime | None
    body_text: str
    in_reply_to: str | None
    references: list[str]
    attachment_names: list[str] = field(default_factory=list)
    content_hash: str = ""

    def thread_message_ids(self) -> list[str]:
        ids = list(self.references)
        if self.in_reply_to and self.in_reply_to not in ids:
            ids.append(self.in_reply_to)
        return ids

    def as_prompt_text(self) -> str:
        """Exactly the text Claude sees; source_text spans are verified against this."""
        lines = [
            f"From: {self.sender.display() if self.sender else '(unknown)'}",
            f"To: {', '.join(a.display() for a in self.to) or '(none)'}",
        ]
        if self.cc:
            lines.append(f"Cc: {', '.join(a.display() for a in self.cc)}")
        lines += [
            f"Date: {self.sent_at.isoformat() if self.sent_at else '(unknown)'}",
            f"Subject: {self.subject or '(no subject)'}",
        ]
        if self.attachment_names:
            lines.append(f"Attachments (not read): {', '.join(self.attachment_names)}")
        return "\n".join(lines) + "\n\n" + self.body_text


def _addresses(msg: EmailMessage, header: str) -> list[Address]:
    values = msg.get_all(header, [])
    return [
        Address(name=name or None, email=addr.lower())
        for name, addr in getaddresses([str(v) for v in values])
        if addr
    ]


def _message_ids(value: str | None) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value or "")


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6])>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        content = _html_to_text(content)
    return content.replace("\r\n", "\n").strip()


def parse_email(raw: bytes) -> ParsedEmail:
    if not raw.strip():
        raise EmailParseError("Email is empty")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if not any(msg.get(h) for h in ("From", "Subject", "Date", "To")):
        raise EmailParseError(
            "No email headers found; upload a .eml file or paste the full raw message source"
        )

    senders = _addresses(msg, "From")
    sent_at = None
    if msg.get("Date"):
        try:
            sent_at = parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            sent_at = None

    message_ids = _message_ids(msg.get("Message-ID"))
    in_reply_to = _message_ids(msg.get("In-Reply-To"))
    body = _body(msg)
    if not body:
        raise EmailParseError("Email has no readable text body")

    return ParsedEmail(
        message_id=message_ids[0] if message_ids else None,
        subject=str(msg.get("Subject", "")).strip(),
        sender=senders[0] if senders else None,
        to=_addresses(msg, "To"),
        cc=_addresses(msg, "Cc"),
        sent_at=sent_at,
        body_text=body,
        in_reply_to=in_reply_to[0] if in_reply_to else None,
        references=_message_ids(msg.get("References")),
        attachment_names=[p.get_filename() for p in msg.iter_attachments() if p.get_filename()],
        content_hash=hashlib.sha256(raw).hexdigest(),
    )
