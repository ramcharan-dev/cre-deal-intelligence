"""Deterministic rule-based extractor for local POC execution.

Extracts commercial real estate (CRE) financing data, property details, and lender quote terms
from email text and headers using regex patterns and heuristics. Every extracted field carries
a verbatim `source_text` slice from the email, guaranteeing valid provenance through the pipeline
without requiring an external LLM or API keys.
"""

from __future__ import annotations

import re
from datetime import date

from app.extraction.fields import DEAL_FIELD_SPECS, QUOTE_FIELD_SPECS
from app.extraction.matching import DealCandidate, normalize_name
from app.extraction.schemas import (
    DealFieldExtraction,
    DealMatch,
    EmailExtraction,
    EmailType,
    LenderQuoteExtraction,
    QuoteFieldExtraction,
    SourcedText,
)
from app.services.email_parser import ParsedEmail

_MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_PROPERTY_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("senior_housing", r"\b(?:senior\s*housing|assisted\s*living|memory\s*care)\b"),
    ("student_housing", r"\b(?:student\s*housing)\b"),
    ("manufactured_housing", r"\b(?:manufactured\s*housing|mobile\s*home\s*park)\b"),
    ("self_storage", r"\b(?:self[- ]?storage|mini[- ]?storage)\b"),
    ("mixed_use", r"\b(?:mixed[- ]?use)\b"),
    ("hospitality", r"\b(?:hospitality|hotels?|motels?|resorts?)\b"),
    ("healthcare", r"\b(?:healthcare|hospitals?|clinics?|medical\s*office|mob)\b"),
    ("industrial", r"\b(?:industrials?|warehouses?|logistics|distribution\s*center|flex)\b"),
    ("multifamily", r"\b(?:multifamily|apartments?|multi[- ]family|units?|mf)\b"),
    ("retail", r"\b(?:retails?|shopping\s*center|strip\s*mall|anchored)\b"),
    ("office", r"\b(?:offices?|corporate\s*center)\b"),
    ("land", r"\b(?:raw\s*land|acreage|land\s*parcel)\b"),
]

_TRANSACTION_TYPE_KEYWORDS: list[tuple[str, str]] = [
    ("refinance", r"\b(?:refinanc\w*|refi\b)"),
    ("acquisition", r"\b(?:acquisitions?|purchas\w*|buying\b)"),
    ("construction", r"\b(?:constructions?|ground[- ]up|development\b)"),
    ("bridge", r"\b(?:bridge\s*loan|bridge\s*financing|transitional\b)"),
    ("recapitalization", r"\b(?:recapitaliz\w*|recap\b)"),
]

_US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

_QUOTED_REPLY_RE = re.compile(
    r"(?:^-{2,}\s*Original Message\s*-{2,}.*|^On\s+.+?wrote:.*|^From:\s*.+?\nSent:\s*.+?\nTo:\s*.+?\nSubject:\s*.*)",
    re.MULTILINE | re.DOTALL,
)
_QUOTED_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)

_DECLINE_RE = re.compile(
    r"\b(we\s*(?:will\s*)?pass(?:ed)?|unable\s*to\s*(?:quote|offer|participate|proceed)|"
    r"pass(?:ing)?\s*(?:on|at\s*this\s*time)?|declined?|turned\s*down|"
    r"out\s*of\s*(?:multifamily|office|retail|industrial)|not\s*a\s*fit|"
    r"credit\s*(?:team\s*)?passed)\b",
    re.IGNORECASE,
)

_CLOSING_RE = re.compile(
    r"\b((?:deal|loan|transaction)\s*(?:has\s*)?closed|closing\s*(?:is\s*)?confirmed|closing\s*notice|loan\s*funded|financing\s*closed)\b",
    re.IGNORECASE,
)

_LENDER_TERMS_RE = re.compile(
    r"\b(term\s*sheet|indicative(?:\s*financing)?\s*terms?|(?:loan|financing)\s*proposal|quote\s*summary|"
    r"pricing\s*indication|lender\s*feedback|pleased\s*to\s*offer)\b",
    re.IGNORECASE,
)

_SUBMISSION_RE = re.compile(
    r"\b(new\s*refi|new\s*acquisition|financing\s*mandate|seeking\s*\$|request\s*for\s*(?:financing|quotes?)|"
    r"loan\s*request|deal\s*submission|offering\s*memorandum|om\s*attached|we\s*have\s*been\s*(?:retained|engaged))\b",
    re.IGNORECASE,
)


def _parse_date_string(s: str) -> str | None:
    """Attempt to parse natural language or ISO date to YYYY-MM-DD."""
    m_iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            return date(y, m, d).isoformat()
        except ValueError:
            return None
    m_word = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", s)
    if m_word:
        month_name = m_word.group(1).lower()
        if month_name in _MONTH_MAP:
            y, m, d = int(m_word.group(3)), _MONTH_MAP[month_name], int(m_word.group(2))
            try:
                return date(y, m, d).isoformat()
            except ValueError:
                return None
    return None


class LocalExtractor:
    """Deterministic CRE extractor that inspects email text directly."""

    model: str = "local-deterministic"

    def __init__(self) -> None:
        pass

    async def extract(self, email: ParsedEmail, candidates: list[DealCandidate]) -> EmailExtraction:
        text = email.as_prompt_text()
        subject = email.subject or ""
        body = email.body_text or ""

        # Clean subject (strip RE:, FW:) and clean body (strip quoted replies) for classification
        clean_subject = re.sub(r"^(?:re|fw|fwd)\s*:\s*", "", subject, flags=re.IGNORECASE).strip()
        is_reply = bool(re.match(r"^(?:re|fw|fwd)\s*:", subject, re.IGNORECASE))
        clean_b = _QUOTED_REPLY_RE.sub("", body)
        new_body = _QUOTED_LINE_RE.sub("", clean_b).strip() or body

        # 1. Match to existing deal candidates
        deal_match = self._match_candidate(subject, body, candidates)

        # 2. Extract deal fields
        deal_fields = self._extract_deal_fields(text, subject, body)

        # 3. Extract quote fields & lenders
        quotes = self._extract_quotes(text, subject, body, email, deal_fields=deal_fields)

        # 4. Classify email type accurately using extracted quotes, clean_subject, and new_body
        email_type = self._detect_email_type(
            subject=clean_subject,
            body=new_body,
            is_reply=is_reply,
            quotes=quotes,
            deal_fields=deal_fields,
            has_matched_deal=deal_match.matched_deal_id is not None,
        )

        # 5. Build summary
        summary = self._generate_summary(subject, email_type, deal_fields, quotes)

        return EmailExtraction(
            email_type=email_type,
            summary=summary,
            deal_match=deal_match,
            deal_fields=deal_fields,
            quotes=quotes,
        )

    def _detect_email_type(
        self,
        subject: str,
        body: str,
        is_reply: bool = False,
        quotes: list[LenderQuoteExtraction] | None = None,
        deal_fields: list[DealFieldExtraction] | None = None,
        has_matched_deal: bool = False,
    ) -> EmailType:
        quotes = quotes or []
        deal_fields = deal_fields or []

        # 1. If valid quotes with quote fields were extracted
        if quotes:
            has_financial_terms = any(
                any(
                    f.field
                    in {
                        "loan_amount",
                        "interest_rate",
                        "rate_type",
                        "spread_bps",
                        "ltv",
                        "ltc",
                        "term_months",
                        "amortization_months",
                        "interest_only_months",
                        "min_dscr",
                        "origination_fee_pct",
                        "recourse",
                    }
                    for f in q.fields
                )
                for q in quotes
            )
            has_decline = any(
                any(f.field == "quote_status" and f.value == "declined" for f in q.fields) for q in quotes
            )
            if has_financial_terms or has_decline:
                return "lender_quote"

        # 2. Check for explicit lender decline in the message
        if _DECLINE_RE.search(body):
            return "lender_quote"

        # 3. Check deal update / closing
        if _CLOSING_RE.search(body):
            return "deal_update"

        # 4. Check lender quote / proposal / terms in new body
        if _LENDER_TERMS_RE.search(body) and is_reply:
            return "lender_quote"

        # 5. Check deal submission / financing request
        # If it's not a reply, or the new body explicitly has mandate/submission language
        combo = f"{subject}\n{body}".lower()
        if not is_reply or _SUBMISSION_RE.search(body.lower()):
            if _SUBMISSION_RE.search(combo):
                return "deal_submission"

        # 6. Check if loan amount was requested and property fields exist without quotes (new mandate)
        field_names = {f.field for f in deal_fields}
        if "loan_amount_requested" in field_names and not quotes and not is_reply:
            return "deal_submission"

        # 7. If it's a reply on an existing deal without quotes or submission
        if is_reply and has_matched_deal:
            return "deal_update"

        return "other"

    def _match_candidate(self, subject: str, body: str, candidates: list[DealCandidate]) -> DealMatch:
        combo = f"{subject}\n{body}"
        norm_combo = normalize_name(combo)

        for c in candidates:
            # Check property name or deal name
            if c.property_name and normalize_name(c.property_name) in norm_combo:
                return DealMatch(
                    matched_deal_id=c.id,
                    confidence="high",
                    reasoning=f"Property name matches candidate '{c.deal_name}'",
                )
            if c.deal_name and normalize_name(c.deal_name) in norm_combo:
                return DealMatch(
                    matched_deal_id=c.id,
                    confidence="high",
                    reasoning=f"Deal name matches candidate '{c.deal_name}'",
                )
            if c.property_address and normalize_name(c.property_address) in norm_combo:
                return DealMatch(
                    matched_deal_id=c.id,
                    confidence="high",
                    reasoning=f"Property address matches candidate '{c.deal_name}'",
                )

        return DealMatch(
            matched_deal_id=None,
            confidence="low",
            reasoning="New deal opportunity (no existing deal candidate matched)",
        )

    def _extract_deal_fields(self, text: str, subject: str, body: str) -> list[DealFieldExtraction]:
        fields: list[DealFieldExtraction] = []
        seen_fields: set[str] = set()

        def add_field(field_name: str, value: str, source_text: str):
            if field_name not in seen_fields and source_text in text and field_name in DEAL_FIELD_SPECS:
                seen_fields.add(field_name)
                fields.append(DealFieldExtraction(field=field_name, value=value, source_text=source_text))

        # 1. Property Name / Deal Name
        # Look for explicit property line
        m_prop = re.search(r"(?i)\b(?:Property|Property\s*Name|Asset)\s*[:–-]\s*([^\n\r,]+)", text)
        if m_prop:
            val = m_prop.group(1).strip()
            add_field("property_name", val, m_prop.group(0))
            add_field("deal_name", val, m_prop.group(0))

        # Subject line extraction (e.g. "New refi mandate - The Lofts at Riverbend (Sacramento MF) - $50MM")
        if "deal_name" not in seen_fields:
            m_subj = re.search(
                r"(?i)(?:mandate|financing|refi|acquisition|closed|update)\s*[-–:]\s*([A-Za-z0-9\s'&]+?)(?:\s*\([^)]*\)|\s*[-–:]|\s*\$\d|\n|$)",
                subject,
            )
            if m_subj:
                name_cand = m_subj.group(1).strip()
                if len(name_cand) > 3:
                    add_field("deal_name", name_cand, m_subj.group(0).strip())
                    add_field("property_name", name_cand, m_subj.group(0).strip())

        # Fallback to Named Buildings pattern: "The Lofts at Riverbend", "Mesa Logistics Center"
        if "deal_name" not in seen_fields:
            m_entity = re.search(
                r"\b((?:The\s+)?[A-Z][a-zA-Z0-9\s&']+(?:Apartments|Lofts|Center|Building|Commons|Park|Plaza|Village|Heights|Towers?|Estates?|Offices?))\b",
                text,
            )
            if m_entity:
                name_cand = m_entity.group(1).strip()
                add_field("deal_name", name_cand, m_entity.group(0))
                add_field("property_name", name_cand, m_entity.group(0))

        # 2. Property Address, City, State
        m_addr = re.search(r"(?i)\b(?:Address|Location|Property\s*Address)\s*[:–-]\s*([^\n\r]+)", text)
        if m_addr:
            full_addr_str = m_addr.group(1).strip()
            add_field("property_address", full_addr_str, m_addr.group(0))
            # Try to extract city and state from comma separation
            parts = [p.strip() for p in full_addr_str.split(",")]
            if len(parts) >= 2:
                # Last part might be "State Zip" or "State"
                last = parts[-1].strip().split()
                if last and last[0].upper() in _US_STATES:
                    add_field("state", last[0].upper(), last[0])
                    add_field("city", parts[-2].strip(), parts[-2])
        else:
            # Inline address search: e.g. "1850 Riverside Drive, Sacramento, CA"
            m_street = re.search(
                r"\b(\d{1,5}\s+[A-Z][A-Za-z0-9\s.,]+?(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Way|Lane|Ln|Court|Ct|Parkway|Pkwy))\b",
                text,
            )
            if m_street:
                add_field("property_address", m_street.group(1).strip(), m_street.group(0))

            m_city_st = re.search(r"\b([A-Z][a-zA-Z\s]+),\s*([A-Z]{2})\b", text)
            if m_city_st and m_city_st.group(2).upper() in _US_STATES:
                add_field("city", m_city_st.group(1).strip(), m_city_st.group(1))
                add_field("state", m_city_st.group(2).upper(), m_city_st.group(2))

        # 3. Property Type
        for ptype, pattern in _PROPERTY_TYPE_KEYWORDS:
            m_pt = re.search(pattern, text, re.IGNORECASE)
            if m_pt:
                add_field("property_type", ptype, m_pt.group(0))
                break

        # 4. Transaction Type
        for ttype, pattern in _TRANSACTION_TYPE_KEYWORDS:
            m_tt = re.search(pattern, text, re.IGNORECASE)
            if m_tt:
                add_field("transaction_type", ttype, m_tt.group(0))
                break

        # 5. Loan Amount Requested
        m_req = re.search(
            r"(?i)\b(?:loan\s*(?:amount|request|size)|seeking|requesting)\s*[:–-]?\s*(\$[\d,.]+\s*(?:mm|m|million|b|k)?)\b",
            text,
        )
        if m_req:
            add_field("loan_amount_requested", m_req.group(1).strip(), m_req.group(0))
        else:
            # In subject: "... - $50MM"
            m_subj_amt = re.search(r"(\$[\d,.]+\s*(?:mm|m|million|b|k)?)\b", subject, re.IGNORECASE)
            if m_subj_amt:
                add_field("loan_amount_requested", m_subj_amt.group(1).strip(), m_subj_amt.group(0))

        # 6. Purchase Price & Property Value
        m_price = re.search(
            r"(?i)\b(?:purchase\s*price|acquisition\s*price)\s*[:–-]?\s*(\$[\d,.]+\s*(?:mm|m|million)?)\b",
            text,
        )
        if m_price:
            add_field("purchase_price", m_price.group(1).strip(), m_price.group(0))

        m_val = re.search(
            r"(?i)\b(?:property\s*value|appraised\s*value|as-is\s*value|valuation)\s*[:–-]?\s*(\$[\d,.]+\s*(?:mm|m|million)?)\b",
            text,
        )
        if m_val:
            add_field("property_value", m_val.group(1).strip(), m_val.group(0))

        # 7. Units, Square Feet, Year Built, Occupancy
        m_units = re.search(r"\b(\d{1,5})\s*(?:units?|keys?|beds?)\b", text, re.IGNORECASE)
        if m_units:
            add_field("units", m_units.group(1), m_units.group(0))

        m_sf = re.search(r"\b([\d,]{3,10})\s*(?:sf|sq\s*ft|square\s*feet)\b", text, re.IGNORECASE)
        if m_sf:
            add_field("square_feet", m_sf.group(1).replace(",", ""), m_sf.group(0))

        m_built = re.search(r"(?i)\b(?:built\s*(?:in)?|year\s*built\s*[:–-]?)\s*(\d{4})\b", text)
        if m_built:
            add_field("year_built", m_built.group(1), m_built.group(0))

        m_occ = re.search(r"\b(\d{1,3}(?:\.\d+)?%?)\s*(?:occupancy|occupied)\b", text, re.IGNORECASE)
        if m_occ:
            add_field("occupancy_pct", m_occ.group(1).rstrip("%"), m_occ.group(0))

        # 8. Target Closing Date
        m_close = re.search(
            r"(?i)\b(?:target\s*closing(?:\s*date)?|closing\s*date|targeting\s*closing\s*(?:on)?)\s*[:–-]?\s*([^\n\r,.]{5,30})",
            text,
        )
        if m_close:
            parsed_d = _parse_date_string(m_close.group(1).strip())
            if parsed_d:
                add_field("target_closing_date", parsed_d, m_close.group(0))

        # 9. Sponsor Name
        m_spons = re.search(
            r"(?i)\b(?:sponsor|borrower)\s*[:–-]\s*([A-Z][A-Za-z0-9\s&,.'-]+?(?:Partners|Capital|LLC|Group|Holdings|Properties|Fund|LP|Corp|Development))",
            text,
        )
        if m_spons:
            add_field("sponsor_name", m_spons.group(1).strip(), m_spons.group(0))

        return fields

    def _extract_quotes(
        self,
        text: str,
        subject: str,
        body: str,
        email: ParsedEmail,
        deal_fields: list[DealFieldExtraction] | None = None,
    ) -> list[LenderQuoteExtraction]:
        quotes: list[LenderQuoteExtraction] = []

        excluded_names = set()
        if deal_fields:
            for df in deal_fields:
                if df.field in ("sponsor_name", "broker_name"):
                    excluded_names.add(normalize_name(df.value))

        # Detect potential lenders mentioned in the email or sender
        known_lenders: list[tuple[str, str]] = []  # (name, source_text)

        # 1. From text mentions: "Beacon Agency Lending", "Pacific Crest Bank", "Pacific Commercial Finance", etc.
        lender_matches = re.finditer(
            r"\b([A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Za-z0-9&'.-]+){0,4}?\s+(?:Bank|Lending|Capital|Debt\s*Partners|Life\s*Insurance(?:\s*Company)?|Credit\s*Union|Financial|Finance|Funding))\b",
            text,
        )
        seen_names: set[str] = set()
        for lm in lender_matches:
            name = lm.group(1).strip()
            # Exclude multi-line spans, generic phrases, and sponsor/broker names
            if (
                "\n" not in name
                and len(name.split()) >= 2
                and normalize_name(name) not in seen_names
                and normalize_name(name) not in excluded_names
            ):
                seen_names.add(normalize_name(name))
                known_lenders.append((name, lm.group(0)))

        # 2. From sender line if lender not found
        if not known_lenders and email.sender:
            sender_name = email.sender.name or ""
            domain = email.sender.email.split("@")[-1].lower() if "@" in email.sender.email else ""
            if any(
                k in sender_name.lower() or k in domain
                for k in ("bank", "lending", "capital", "partners", "credit", "finance", "funding")
            ):
                lender_display = sender_name or domain.split(".")[0].title()
                src = sender_name if sender_name and sender_name in text else email.sender.email
                if src in text and normalize_name(lender_display) not in excluded_names:
                    known_lenders.append((lender_display, src))

        decline_match = _DECLINE_RE.search(text)
        if not known_lenders:
            # Check if there is a generic decline or proposal
            if re.search(r"\b(term\s*sheet|proposal)\b", text, re.IGNORECASE) or decline_match:
                m_inst = re.search(
                    r"\b([A-Z][A-Za-z0-9&'.-]{2,30}(?:\s+[A-Za-z0-9&'.-]+)?\s+(?:Bank|Lenders?|Capital|Partners|Finance|Funding))\b",
                    text,
                )
                if m_inst and normalize_name(m_inst.group(1).strip()) not in excluded_names:
                    known_lenders.append((m_inst.group(1).strip(), m_inst.group(0)))
                elif email.sender:
                    s_name = email.sender.name or email.sender.email.split("@")[0].replace(".", " ").title()
                    src = (
                        email.sender.name
                        if email.sender.name and email.sender.name in text
                        else email.sender.email
                    )
                    if normalize_name(s_name) not in excluded_names:
                        known_lenders.append((s_name, src))

        for lender_name, lender_source in known_lenders:
            quote_fields: list[QuoteFieldExtraction] = []
            seen_qfields: set[str] = set()

            def add_qfield(
                field_name: str,
                value: str,
                source_text: str,
                seen: set[str] = seen_qfields,
                out: list[QuoteFieldExtraction] = quote_fields,
            ):
                if field_name not in seen and source_text in text and field_name in QUOTE_FIELD_SPECS:
                    seen.add(field_name)
                    out.append(QuoteFieldExtraction(field=field_name, value=value, source_text=source_text))

            # Quote Status: declined vs term_sheet vs indicative
            if decline_match:
                add_qfield("quote_status", "declined", decline_match.group(0))
            elif re.search(r"\bterm\s*sheet\b", text, re.IGNORECASE):
                m_ts = re.search(r"\bterm\s*sheet\b", text, re.IGNORECASE)
                if m_ts:
                    add_qfield("quote_status", "term_sheet", m_ts.group(0))
            else:
                m_ind = re.search(
                    r"\b(?:indicative(?:\s*financing)?(?:\s*terms?)?|(?:loan|financing)\s*proposal|quote\s*summary)\b",
                    text,
                    re.IGNORECASE,
                )
                if m_ind:
                    add_qfield("quote_status", "indicative", m_ind.group(0))

            # Loan Amount
            m_lamt = re.search(
                r"(?i)\b(?:loan\s*amount|max\s*loan|proceeds)\s*[:–-]?\s*(\$[\d,.]+(?:\s*(?:mm|m|million|b|k))?)",
                text,
            )
            if m_lamt:
                add_qfield("loan_amount", m_lamt.group(1).strip(), m_lamt.group(0))

            # Interest Rate
            m_irate = re.search(r"(?i)\b(?:rate|interest\s*rate|coupon)[^\n\r%]*?([0-9.]+\s*%)", text)
            if m_irate:
                add_qfield("interest_rate", m_irate.group(1).rstrip("%").strip(), m_irate.group(0))
            else:
                m_at_rate = re.search(r"\bat\s*([0-9.]+\s*%)\b", text, re.IGNORECASE)
                if m_at_rate:
                    add_qfield("interest_rate", m_at_rate.group(1).rstrip("%").strip(), m_at_rate.group(0))

            # Rate Type: fixed vs floating
            m_fixed = re.search(r"\bfixed\b", text, re.IGNORECASE)
            m_floating = re.search(r"\bfloating\b", text, re.IGNORECASE)
            if m_fixed:
                add_qfield("rate_type", "fixed", m_fixed.group(0))
            elif m_floating:
                add_qfield("rate_type", "floating", m_floating.group(0))

            # Spread (bps)
            m_spread = re.search(
                r"\b(?:\+|spread\s*of\s*|spread\s*[:–-]?\s*)(\d{2,4})\s*(?:bps|basis\s*points)\b",
                text,
                re.IGNORECASE,
            )
            if m_spread:
                add_qfield("spread_bps", m_spread.group(1), m_spread.group(0))

            # Index
            m_index = re.search(
                r"\b(1M\s*Term\s*SOFR|SOFR|5Y\s*UST|10Y\s*UST|UST|Treasury)\b", text, re.IGNORECASE
            )
            if m_index:
                add_qfield("index_name", m_index.group(1).strip(), m_index.group(0))

            # Term (months)
            m_term = re.search(r"(?i)\bterm\s*[:–-]?[ \t]*(\d{1,2})[ \t]*(?:year|yr)s?", text)
            if not m_term:
                m_term = re.search(
                    r"\b(\d{1,2})[ \t]*(?:-|–|[ \t]+)(?:year|yr)s?\b(?![ \t]*amortization)",
                    text,
                    re.IGNORECASE,
                )
            if m_term:
                years = int(m_term.group(1))
                add_qfield("term_months", str(years * 12), m_term.group(0))

            # Amortization (months)
            m_amort = re.search(
                r"(?i)\b(?:amortization|amort)\s*[:–-]?[ \t]*(\d{1,2})[ \t]*(?:year|yr)s?", text
            )
            if not m_amort:
                m_amort = re.search(
                    r"\b(\d{1,2})[ \t]*(?:-|–|[ \t]+)(?:year|yr)s?[ \t]+(?:amortization|amort)\b",
                    text,
                    re.IGNORECASE,
                )
            if m_amort:
                add_qfield("amortization_months", str(int(m_amort.group(1)) * 12), m_amort.group(0))

            # Interest-only (months)
            m_io = re.search(
                r"(?i)\b(?:io|interest[- ]only)\s*[:–-]?[ \t]*(\d{1,2})[ \t]*(?:year|yr)s?", text
            )
            if not m_io:
                m_io = re.search(
                    r"\b(\d{1,2})[ \t]*(?:-|–|[ \t]+)(?:year|yr)s?[ \t]+(?:io|interest-only|interest[ \t]+only)\b",
                    text,
                    re.IGNORECASE,
                )
            if m_io:
                add_qfield("interest_only_months", str(int(m_io.group(1)) * 12), m_io.group(0))

            # LTV
            m_ltv = re.search(r"(?i)\bltv\s*[:–-]?\s*(\d{1,2}(?:\.\d+)?%?)", text)
            if not m_ltv:
                m_ltv = re.search(r"\b(\d{1,2}(?:\.\d+)?%?)\s*ltv\b", text, re.IGNORECASE)
            if m_ltv:
                add_qfield("ltv", m_ltv.group(1).rstrip("%").strip(), m_ltv.group(0))

            # Min DSCR
            m_dscr = re.search(r"(?i)\b(?:min\s*dscr|dscr)\s*[:–-]?\s*(\d(?:\.\d+)?x?)\b", text)
            if m_dscr:
                add_qfield("min_dscr", m_dscr.group(1).rstrip("x").strip(), m_dscr.group(0))

            # Origination Fee
            m_fee = re.search(r"(?i)\b(?:origination\s*fee|fee)\s*[:–-]?\s*(\d(?:\.\d+)?%?)\b", text)
            if m_fee:
                add_qfield("origination_fee_pct", m_fee.group(1).rstrip("%").strip(), m_fee.group(0))

            # Recourse
            m_rec = re.search(r"\b(non[- ]recourse|full[- ]recourse)\b", text, re.IGNORECASE)
            if m_rec:
                rec_val = "non_recourse" if "non" in m_rec.group(0).lower() else "full_recourse"
                add_qfield("recourse", rec_val, m_rec.group(0))

            # Prepayment
            m_prepay = re.search(r"(?i)\bprepay(?:ment)?(?:\s*terms?)?\s*[:–-]\s*([^\n\r]{3,160})", text)
            if m_prepay:
                add_qfield("prepayment_terms", m_prepay.group(1).strip(), m_prepay.group(0))

            # Security / collateral
            m_sec = re.search(r"(?i)\b(?:security|collateral)\s*[:–-]\s*([^\n\r]{5,160})", text)
            if m_sec:
                add_qfield("security", m_sec.group(1).strip(), m_sec.group(0))

            # Conditions / covenants
            m_cond = re.search(r"(?i)\b(?:conditions?|covenants?)\s*[:–-]\s*([^\n\r]{5,200})", text)
            if not m_cond:
                m_cond = re.search(r"(?i)\bsubject\s+to\s+([^\n\r.]{5,160})", text)
            if m_cond:
                add_qfield("conditions", m_cond.group(1).strip(), m_cond.group(0))

            # Expiration date
            m_exp = re.search(r"(?i)\b(?:expires?|expiration(?:\s*date)?)\s*[:–-]?\s*([^\n\r,.]{5,30})", text)
            if m_exp:
                exp_d = _parse_date_string(m_exp.group(1).strip())
                if exp_d:
                    add_qfield("expiration_date", exp_d, m_exp.group(0))

            # Contact name / email
            contact_name = None
            contact_email = None
            if email.sender and email.sender.email and email.sender.email in text:
                contact_email = SourcedText(value=email.sender.email, source_text=email.sender.email)
            if email.sender and email.sender.name and email.sender.name in text:
                contact_name = SourcedText(value=email.sender.name, source_text=email.sender.name)

            has_financial_terms = any(
                f.field
                in {
                    "loan_amount",
                    "interest_rate",
                    "rate_type",
                    "spread_bps",
                    "ltv",
                    "ltc",
                    "term_months",
                    "amortization_months",
                    "interest_only_months",
                    "min_dscr",
                    "origination_fee_pct",
                    "recourse",
                }
                for f in quote_fields
            )
            is_decline = any(f.field == "quote_status" and f.value == "declined" for f in quote_fields)

            if is_decline or has_financial_terms:
                quotes.append(
                    LenderQuoteExtraction(
                        lender_name=SourcedText(value=lender_name, source_text=lender_source),
                        lender_contact_name=contact_name,
                        lender_contact_email=contact_email,
                        option_label=None,
                        fields=quote_fields,
                    )
                )

        return quotes

    def _generate_summary(
        self,
        subject: str,
        email_type: EmailType,
        deal_fields: list[DealFieldExtraction],
        quotes: list[LenderQuoteExtraction],
    ) -> str:
        deal_dict = {f.field: f.value for f in deal_fields}
        name = deal_dict.get("deal_name") or deal_dict.get("property_name") or subject or "CRE opportunity"
        amt = deal_dict.get("loan_amount_requested")

        if email_type == "deal_submission":
            if amt:
                return f"Deal financing submission for {name} requesting {amt}."
            return f"Deal financing submission for {name}."

        if email_type == "lender_quote":
            lenders = (
                ", ".join(q.lender_name.value for q in quotes if q.lender_name and q.lender_name.value)
                if quotes
                else "Lender"
            )
            has_decline = any(
                any(f.field == "quote_status" and f.value == "declined" for f in q.fields) for q in quotes
            )
            if has_decline:
                return f"{lenders} declined financing on {name}."
            return f"Lender financing quote and terms from {lenders} for {name}."

        if email_type == "deal_update":
            return f"Deal status update for {name}."

        return f"Commercial real estate email regarding {name}."
