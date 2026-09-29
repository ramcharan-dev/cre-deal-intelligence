"""Helpers for building raw test emails."""

from email.message import EmailMessage


def make_eml(
    subject: str,
    body: str,
    *,
    message_id: str,
    sender: str = "Jane Broker <jane@brokerco.com>",
    to: str = "Deal Team <deals@ourfirm.com>",
    cc: str | None = None,
    date: str = "Mon, 21 Sep 2026 09:30:00 -0500",
    in_reply_to: str | None = None,
    html: str | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = date
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg.as_bytes()
