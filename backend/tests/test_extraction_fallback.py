"""Tests for Gemini → Groq → Deterministic extraction fallback pipeline."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.extraction.fallback import FallbackExtractorRouter, GeminiExtractor, GroqExtractor
from app.extraction.matching import DealCandidate
from app.extraction.providers import create_extractor
from app.extraction.schemas import EmailExtraction
from app.services.email_parser import parse_email

MOCK_EXTRACTION_DICT = {
    "email_type": "lender_quote",
    "summary": "Quote for Oaks Apartments",
    "deal_match": {
        "matched_deal_id": "deal-1",
        "confidence": "high",
        "reasoning": "Name matches The Oaks",
    },
    "deal_fields": [],
    "quotes": [],
}

MOCK_EXTRACTION = EmailExtraction.model_validate(MOCK_EXTRACTION_DICT)

MOCK_GEMINI_SUCCESS = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": json.dumps(MOCK_EXTRACTION_DICT)
                    }
                ]
            }
        }
    ]
}

MOCK_GROQ_SUCCESS = {
    "choices": [
        {
            "message": {
                "content": json.dumps(MOCK_EXTRACTION_DICT)
            }
        }
    ]
}

SAMPLE_EMAIL_BYTES = (
    b"From: broker@capital.com\r\n"
    b"To: team@acquisitions.com\r\n"
    b"Subject: The Oaks Financing\r\n"
    b"Date: Mon, 15 Jan 2024 10:00:00 +0000\r\n"
    b"\r\n"
    b"Seeking financing for The Oaks Apartments, Austin TX. Multifamily, 160 units."
)


def _make_settings(gemini_key: str = "fake-gemini-key", groq_key: str = "fake-groq-key") -> Settings:
    return Settings(
        gemini_api_key=SecretStr(gemini_key) if gemini_key else None,
        groq_api_key=SecretStr(groq_key) if groq_key else None,
        gemini_model="gemini-2.5-flash",
        groq_model="llama-3.3-70b-versatile",
    )


@pytest.fixture
def parsed_email():
    return parse_email(SAMPLE_EMAIL_BYTES)


@pytest.fixture
def candidates():
    return [DealCandidate(id="deal-1", deal_name="The Oaks")]


@pytest.mark.asyncio
async def test_case_1_gemini_succeeds(parsed_email, candidates):
    """Case 1 — Gemini succeeds → extraction returned with gemini model."""
    settings = _make_settings()
    mock_resp = httpx.Response(200, json=MOCK_GEMINI_SUCCESS, request=httpx.Request("POST", "https://api.fake"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        router = FallbackExtractorRouter(
            gemini=GeminiExtractor(settings=settings),
            groq=GroqExtractor(settings=settings),
        )
        res = await router.extract(parsed_email, candidates)

        assert res.email_type == "lender_quote"
        assert router.model == "gemini-2.5-flash"
        assert mock_post.call_count == 1
        # Confirms only 1 Gemini model was requested
        assert "gemini-2.5-flash:generateContent" in str(mock_post.call_args)


@pytest.mark.asyncio
async def test_case_2_gemini_fails_quota_groq_succeeds(parsed_email, candidates):
    """Case 2 — Gemini fails with 429 quota → immediately tries Groq → returns Groq extraction."""
    settings = _make_settings()

    gemini_resp_429 = httpx.Response(
        429,
        json={"error": {"message": "Resource has been exhausted"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    groq_resp_200 = httpx.Response(
        200,
        json=MOCK_GROQ_SUCCESS,
        request=httpx.Request("POST", "https://api.groq.com"),
    )

    async def mock_post(url, **kwargs):
        if "generativelanguage" in str(url):
            return gemini_resp_429
        if "api.groq.com" in str(url):
            return groq_resp_200
        raise ValueError(f"Unexpected url {url}")

    with patch("httpx.AsyncClient.post", side_effect=mock_post) as post_mock:
        router = FallbackExtractorRouter(
            gemini=GeminiExtractor(settings=settings),
            groq=GroqExtractor(settings=settings),
        )
        res = await router.extract(parsed_email, candidates)

        assert res.email_type == "lender_quote"
        assert router.model == "llama-3.3-70b-versatile"
        # Called Gemini once, did not loop through multiple Gemini models, then called Groq
        assert post_mock.call_count == 2


@pytest.mark.asyncio
async def test_case_3_gemini_and_groq_fail_deterministic_fallback(parsed_email, candidates):
    """Case 3 — Gemini and Groq fail → falls back to deterministic extraction."""
    settings = _make_settings()

    gemini_resp_500 = httpx.Response(
        500,
        text="Internal Server Error",
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    groq_resp_503 = httpx.Response(
        503,
        text="Service Unavailable",
        request=httpx.Request("POST", "https://api.groq.com"),
    )

    async def mock_post(url, **kwargs):
        if "generativelanguage" in str(url):
            return gemini_resp_500
        if "api.groq.com" in str(url):
            return groq_resp_503
        raise ValueError(f"Unexpected url {url}")

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        router = FallbackExtractorRouter(
            gemini=GeminiExtractor(settings=settings),
            groq=GroqExtractor(settings=settings),
        )
        res = await router.extract(parsed_email, candidates)

        assert router.model == "local-fallback"
        assert isinstance(res, EmailExtraction)


@pytest.mark.asyncio
async def test_case_4_gemini_timeout_handled_groq_succeeds(parsed_email, candidates):
    """Case 4 — Gemini ReadTimeout is caught and handled, immediately continuing to Groq."""
    settings = _make_settings()

    groq_resp_200 = httpx.Response(
        200,
        json=MOCK_GROQ_SUCCESS,
        request=httpx.Request("POST", "https://api.groq.com"),
    )

    async def mock_post(url, **kwargs):
        if "generativelanguage" in str(url):
            raise httpx.ReadTimeout("The read operation timed out")
        if "api.groq.com" in str(url):
            return groq_resp_200
        raise ValueError(f"Unexpected url {url}")

    with patch("httpx.AsyncClient.post", side_effect=mock_post) as post_mock:
        router = FallbackExtractorRouter(
            gemini=GeminiExtractor(settings=settings),
            groq=GroqExtractor(settings=settings),
        )
        res = await router.extract(parsed_email, candidates)

        assert res.email_type == "lender_quote"
        assert router.model == "llama-3.3-70b-versatile"
        assert post_mock.call_count == 2


@pytest.mark.asyncio
async def test_case_4b_groq_timeout_handled_deterministic_succeeds(parsed_email, candidates):
    """Case 4b — Both Gemini and Groq time out → deterministic fallback succeeds, no unhandled ReadTimeout."""
    settings = _make_settings()

    async def mock_post(url, **kwargs):
        raise httpx.ReadTimeout("Socket read timed out")

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        router = FallbackExtractorRouter(
            gemini=GeminiExtractor(settings=settings),
            groq=GroqExtractor(settings=settings),
        )
        res = await router.extract(parsed_email, candidates)

        assert router.model == "local-fallback"
        assert isinstance(res, EmailExtraction)


def test_create_extractor_wiring():
    """Verify that create_extractor returns FallbackExtractorRouter for 'gemini', 'fallback', and 'groq'."""
    with patch("app.extraction.providers.get_settings") as mock_settings:
        mock_settings.return_value = Settings(llm_provider="gemini")
        extractor = create_extractor()
        assert isinstance(extractor, FallbackExtractorRouter)
        assert extractor.primary == "gemini"

    with patch("app.extraction.providers.get_settings") as mock_settings:
        mock_settings.return_value = Settings(llm_provider="fallback")
        extractor = create_extractor()
        assert isinstance(extractor, FallbackExtractorRouter)

    with patch("app.extraction.providers.get_settings") as mock_settings:
        mock_settings.return_value = Settings(llm_provider="groq")
        extractor = create_extractor()
        assert isinstance(extractor, FallbackExtractorRouter)
        assert extractor.primary == "groq"


def test_api_emails_gemini_fails_groq_succeeds(clean_db):
    """Integration test: POST /api/emails when Gemini 429s, Groq succeeds -> HTTP 200."""
    from starlette.testclient import TestClient
    from app.main import app

    gemini_resp_429 = httpx.Response(
        429,
        json={"error": {"message": "Resource exhausted"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    groq_resp_200 = httpx.Response(
        200,
        json=MOCK_GROQ_SUCCESS,
        request=httpx.Request("POST", "https://api.groq.com"),
    )

    async def mock_post(url, **kwargs):
        if "generativelanguage" in str(url):
            return gemini_resp_429
        if "api.groq.com" in str(url):
            return groq_resp_200
        raise ValueError(f"Unexpected url {url}")

    fake_settings = Settings(
        llm_provider="gemini",
        gemini_api_key=SecretStr("fake-gemini"),
        groq_api_key=SecretStr("fake-groq"),
    )

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        with patch("app.core.config.get_settings", return_value=fake_settings):
            with patch("app.extraction.fallback.get_settings", return_value=fake_settings):
                with patch("app.extraction.providers.get_settings", return_value=fake_settings):
                    with TestClient(app) as client:
                        resp = client.post(
                            "/api/emails",
                            files={"file": ("test.eml", SAMPLE_EMAIL_BYTES, "message/rfc822")},
                        )
                        assert resp.status_code == 200
                        body = resp.json()
                        assert body["model"] == "llama-3.3-70b-versatile"
                        assert body["deal"]["created"] is True


def test_api_emails_gemini_and_groq_timeout_deterministic_succeeds(clean_db):
    """Integration test: POST /api/emails when both Gemini & Groq timeout -> LocalExtractor -> HTTP 200."""
    from starlette.testclient import TestClient
    from app.main import app

    async def mock_post(url, **kwargs):
        raise httpx.ReadTimeout("Read timed out")

    fake_settings = Settings(
        llm_provider="gemini",
        gemini_api_key=SecretStr("fake-gemini"),
        groq_api_key=SecretStr("fake-groq"),
    )

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        with patch("app.core.config.get_settings", return_value=fake_settings):
            with patch("app.extraction.fallback.get_settings", return_value=fake_settings):
                with patch("app.extraction.providers.get_settings", return_value=fake_settings):
                    with TestClient(app) as client:
                        resp = client.post(
                            "/api/emails",
                            files={"file": ("test.eml", SAMPLE_EMAIL_BYTES, "message/rfc822")},
                        )
                        assert resp.status_code == 200
                        body = resp.json()
                        assert body["model"] == "local-fallback"
                        assert body["deal"]["created"] is True

