from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from app.services.email_parser import EmailParseError, parse_email
from tests.emails import make_eml


def test_parses_headers_recipients_and_body() -> None:
    raw = make_eml(
        "Parkside refi",
        "Seeking $12.5MM.\n",
        message_id="<m1@brokerco.com>",
        to="Deal Team <deals@ourfirm.com>, ops@ourfirm.com",
        cc="Bob <BOB@Sponsor.com>",
    )
    p = parse_email(raw)
    assert p.subject == "Parkside refi"
    assert p.sender and (p.sender.name, p.sender.email) == ("Jane Broker", "jane@brokerco.com")
    assert [a.email for a in p.to] == ["deals@ourfirm.com", "ops@ourfirm.com"]
    assert [(a.name, a.email) for a in p.cc] == [("Bob", "bob@sponsor.com")]
    assert p.sent_at == datetime(2026, 9, 21, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert p.body_text == "Seeking $12.5MM."
    assert p.message_id == "<m1@brokerco.com>"
    assert len(p.content_hash) == 64


def test_prompt_text_contains_headers_and_body() -> None:
    text = parse_email(make_eml("Subj", "Body line", message_id="<m@x>")).as_prompt_text()
    assert "From: Jane Broker <jane@brokerco.com>" in text
    assert "Subject: Subj" in text
    assert text.endswith("Body line")


def test_thread_ids_from_reply_headers() -> None:
    p = parse_email(make_eml("Re: x", "ok", message_id="<m2@x>", in_reply_to="<m1@x>"))
    assert p.in_reply_to == "<m1@x>"
    assert p.thread_message_ids() == ["<m1@x>"]


def test_html_only_email_is_converted_to_text() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "html"
    msg.set_content(
        "<html><style>p{}</style><body><p>Rate: 6.25%</p><p>Term&nbsp;5 yrs</p></body></html>", subtype="html"
    )
    body = parse_email(msg.as_bytes()).body_text
    assert "Rate: 6.25%" in body and "Term 5 yrs" in body and "p{}" not in body


def test_prefers_plain_text_part() -> None:
    raw = make_eml("s", "plain version", message_id="<m@x>", html="<p>html version</p>")
    assert parse_email(raw).body_text == "plain version"


def test_attachments_listed() -> None:
    msg = EmailMessage()
    msg["From"] = "a@b.com"
    msg["Subject"] = "OM attached"
    msg.set_content("See attached")
    msg.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="OM.pdf")
    assert parse_email(msg.as_bytes()).attachment_names == ["OM.pdf"]


@pytest.mark.parametrize("raw", [b"", b"   ", b"just some text with no headers at all"])
def test_rejects_non_emails(raw: bytes) -> None:
    with pytest.raises(EmailParseError):
        parse_email(raw)
