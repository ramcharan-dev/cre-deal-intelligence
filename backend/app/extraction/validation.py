"""Deterministic checks on Claude's extraction before anything is persisted.

Every accepted value must (1) coerce to its field's type and bounds and (2) carry a source_text that
actually appears in the email. Anything that fails is dropped and reported as a ValidationIssue.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from app.extraction.fields import DEAL_FIELD_SPECS, QUOTE_FIELD_SPECS, FieldSpec, FieldType
from app.extraction.schemas import (
    DealMatch,
    EmailExtraction,
    SourcedText,
    TypedValue,
    ValidatedExtraction,
    ValidatedField,
    ValidatedLenderQuote,
    ValidationIssue,
)

MAX_TEXT_LEN = 500

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "mm": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000}
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


class CoercionError(ValueError):
    pass


def normalize_for_match(text: str) -> str:
    """Case/whitespace/quote-marker insensitive form used to verify source_text spans."""
    text = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    text = re.sub(r"(?m)^[ \t]*(?:>[ \t]?)+", "", text)  # strip reply quote markers
    return re.sub(r"\s+", " ", text).strip().lower()


def source_in_email(source_text: str, email_text_normalized: str) -> bool:
    needle = normalize_for_match(source_text)
    return bool(needle) and needle in email_text_normalized


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise CoercionError(f"not a number: {raw!r}") from exc


def coerce_value(spec: FieldSpec, raw: str) -> TypedValue:
    s = raw.strip()
    if not s:
        raise CoercionError("empty value")

    match spec.type:
        case FieldType.TEXT:
            if len(s) > MAX_TEXT_LEN:
                raise CoercionError(f"text longer than {MAX_TEXT_LEN} characters")
            return s
        case FieldType.ENUM:
            v = re.sub(r"[\s\-]+", "_", s.lower())
            if v not in spec.choices:
                raise CoercionError(f"{s!r} is not one of {', '.join(spec.choices)}")
            return v
        case FieldType.DATE:
            try:
                return date.fromisoformat(s)
            except ValueError as exc:
                raise CoercionError(f"not an ISO date (YYYY-MM-DD): {s!r}") from exc
        case FieldType.MONEY:
            m = re.fullmatch(r"(-?[\d.]+)\s*(k|mm|m|bn|b)?", re.sub(r"[$,\s]|usd", "", s.lower()))
            if not m:
                raise CoercionError(f"not a dollar amount: {s!r}")
            value = _decimal(m.group(1)) * _MULTIPLIERS.get(m.group(2) or "", 1)
        case FieldType.PERCENT:
            value = _decimal(s.rstrip("%").strip())
        case FieldType.BPS:
            value = _decimal(re.sub(r"\s*(bps|bp)$", "", s.lower()))
        case FieldType.RATIO:
            value = _decimal(s.lower().rstrip("x").strip())
        case FieldType.INTEGER:
            value = _decimal(s.replace(",", ""))
            if value != value.to_integral_value():
                raise CoercionError(f"not a whole number: {s!r}")

    if spec.min is not None and value < spec.min:
        raise CoercionError(f"{value} is below minimum {spec.min}")
    if spec.max is not None and value > spec.max:
        raise CoercionError(f"{value} is above maximum {spec.max}")
    return int(value) if spec.type == FieldType.INTEGER else value


def _validate_fields(items, specs: dict[str, FieldSpec], scope: str, email_norm: str, issues: list):
    accepted: list[ValidatedField] = []
    seen: set[str] = set()
    for item in items:
        if item.field in seen:
            issues.append(
                ValidationIssue(
                    scope=scope, field=item.field, reason="duplicate field; kept first", raw_value=item.value
                )
            )
            continue
        if not source_in_email(item.source_text, email_norm):
            issues.append(
                ValidationIssue(
                    scope=scope,
                    field=item.field,
                    reason="source_text not found in email",
                    raw_value=item.value,
                )
            )
            continue
        try:
            value = coerce_value(specs[item.field], item.value)
        except CoercionError as exc:
            issues.append(
                ValidationIssue(scope=scope, field=item.field, reason=str(exc), raw_value=item.value)
            )
            continue
        seen.add(item.field)
        accepted.append(
            ValidatedField(
                field=item.field, value=value, raw_value=item.value, source_text=item.source_text.strip()
            )
        )
    return accepted


def _sourced(item: SourcedText | None, field: str, scope: str, email_norm: str, issues: list, *, email=False):
    if item is None or not item.value.strip():
        return None
    if not source_in_email(item.source_text, email_norm):
        issues.append(
            ValidationIssue(
                scope=scope, field=field, reason="source_text not found in email", raw_value=item.value
            )
        )
        return None
    value = item.value.strip()
    if email and not _EMAIL_RE.match(value):
        issues.append(
            ValidationIssue(scope=scope, field=field, reason="not an email address", raw_value=value)
        )
        return None
    return ValidatedField(
        field=field, value=value, raw_value=item.value, source_text=item.source_text.strip()
    )


def validate_extraction(
    extraction: EmailExtraction, email_text: str, candidate_deal_ids: set[str]
) -> ValidatedExtraction:
    """`email_text` must be exactly the text Claude was shown (headers + body)."""
    email_norm = normalize_for_match(email_text)
    issues: list[ValidationIssue] = []

    deal_fields = _validate_fields(extraction.deal_fields, DEAL_FIELD_SPECS, "deal", email_norm, issues)

    quotes: list[ValidatedLenderQuote] = []
    for i, q in enumerate(extraction.quotes):
        scope = f"quote[{i}]"
        lender = _sourced(q.lender_name, "lender_name", scope, email_norm, issues)
        if lender is None:
            issues.append(
                ValidationIssue(
                    scope=scope, field="lender_name", reason="quote dropped: no verifiable lender"
                )
            )
            continue
        quotes.append(
            ValidatedLenderQuote(
                lender_name=str(lender.value),
                lender_name_source=lender.source_text,
                contact_name=_sourced(
                    q.lender_contact_name, "lender_contact_name", scope, email_norm, issues
                ),
                contact_email=_sourced(
                    q.lender_contact_email, "lender_contact_email", scope, email_norm, issues, email=True
                ),
                option_label=(q.option_label or "").strip() or None,
                fields=_validate_fields(q.fields, QUOTE_FIELD_SPECS, scope, email_norm, issues),
            )
        )

    match = extraction.deal_match
    if match.matched_deal_id is not None and match.matched_deal_id not in candidate_deal_ids:
        issues.append(
            ValidationIssue(
                scope="deal_match",
                field="matched_deal_id",
                reason="not one of the candidate deals; ignored",
                raw_value=match.matched_deal_id,
            )
        )
        match = DealMatch(matched_deal_id=None, confidence="low", reasoning=match.reasoning)

    return ValidatedExtraction(
        email_type=extraction.email_type,
        summary=extraction.summary.strip(),
        deal_match=match,
        deal_fields=deal_fields,
        quotes=quotes,
        issues=issues,
    )
