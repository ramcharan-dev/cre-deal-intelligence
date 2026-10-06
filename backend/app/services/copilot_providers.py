"""Multi-provider AI architecture with fallback for CRE Copilot.

Pipeline:
User Question → Context Builder → Gemini Provider → Groq Provider → Deterministic Provider.

If Gemini fails (timeout, rate limit, API error, unconfigured), it falls back to Groq.
If Groq also fails or is unconfigured, it falls back to the existing deterministic/search logic.
Source references and grounding items are preserved directly from retrieved database records.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.api.schemas import AnswerItem, CopilotAnswer, SearchHit
from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Deal

logger = logging.getLogger("app.copilot.providers")

COPILOT_SYSTEM_PROMPT = (
    "You are the Commercial Real Estate (CRE) Deal Intelligence Copilot.\n"
    "Your role is to answer questions about commercial real estate deals, lender quotes, loan terms, and transactions accurately and concisely.\n\n"
    "STRICT GROUNDING INSTRUCTIONS:\n"
    "1. Answer ONLY using the facts stated in the retrieved context below.\n"
    "2. Do NOT extrapolate, speculate, or fabricate any interest rates, loan proceeds, lender names, dates, or terms.\n"
    "3. If the retrieved context does not contain enough information to answer the question, state: "
    "'The requested information was not found in the available deal records and emails.'\n"
    "4. When stating transactional facts (such as interest rates, proceeds, or lender quotes), reference the source lender and email date as stated in the context.\n"
    "5. Do NOT invent external links, references, or IDs.\n"
)


@dataclass
class CopilotContext:
    question: str
    deal_id: uuid.UUID | None
    deal_name: str | None
    intent: str
    items: list[AnswerItem] = field(default_factory=list)
    context_text: str = ""
    db: AsyncSession | None = None
    today: date | None = None
    deal: Deal | None = None


def format_search_context(hits: list[SearchHit]) -> tuple[str, list[AnswerItem]]:
    """Builds formatted prompt context text and authentic AnswerItems from database SearchHits."""
    items: list[AnswerItem] = []
    lines: list[str] = []

    for i, h in enumerate(hits, 1):
        item = AnswerItem(
            title=h.title + (f" · {h.deal_name}" if h.deal_name and h.kind != "deal" else ""),
            text=h.snippet,
            deal_id=h.deal_id,
            sources=[h.source] if h.source else [],
        )
        items.append(item)

        source_info = ""
        if h.source:
            sender = f" from {h.source.email_sender}" if h.source.email_sender else ""
            sent = f" ({h.source.email_sent_at.strftime('%Y-%m-%d')})" if h.source.email_sent_at else ""
            source_info = f' | Source: "{h.source.email_subject}"{sender}{sent}'

        lines.append(f"[{i}] ({h.kind.upper()}) {item.title}\nContent: {h.snippet}{source_info}")

    context_text = "\n\n".join(lines) if lines else "No matching records found in database."
    return context_text, items


def build_user_prompt(question: str, context_text: str, deal_name: str | None = None) -> str:
    deal_header = f"Scope: Deal '{deal_name}'\n" if deal_name else "Scope: Active Deals / Portfolio\n"
    return (
        f"{deal_header}User Question: {question}\n\nRetrieved Records & Context:\n{context_text}\n\nAnswer:"
    )


class CopilotProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    async def answer(self, context: CopilotContext) -> CopilotAnswer: ...


class GeminiCopilotProvider:
    name: str = "gemini"

    def __init__(
        self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.http_client = http_client
        self.timeout_seconds = self.settings.gemini_timeout_seconds

    def is_configured(self) -> bool:
        key = self.settings.gemini_api_key
        return key is not None and bool(key.get_secret_value().strip())

    async def answer(self, context: CopilotContext) -> CopilotAnswer:
        if not self.is_configured():
            raise RuntimeError("Gemini is not configured")

        key = self.settings.gemini_api_key.get_secret_value().strip()  # type: ignore[union-attr]
        configured_model = self.settings.gemini_model.strip() or "gemini-2.5-flash"
        models_to_try = [configured_model]
        for fallback_cand in ("gemini-flash-latest", "gemini-3-flash-preview", "gemini-3.5-flash"):
            if fallback_cand not in models_to_try:
                models_to_try.append(fallback_cand)

        prompt = build_user_prompt(context.question, context.context_text, context.deal_name)
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": COPILOT_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2048,
            },
        }

        headers = {
            "x-goog-api-key": key,
            "Content-Type": "application/json",
        }

        per_attempt_timeout = min(self.timeout_seconds, 15.0)
        timeout = httpx.Timeout(per_attempt_timeout, connect=5.0)
        resp: httpx.Response | None = None

        for current_model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent"
            try:
                if self.http_client:
                    resp = await self.http_client.post(url, json=payload, headers=headers, timeout=timeout)
                else:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        resp = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException:
                if current_model != models_to_try[-1]:
                    next_cand = models_to_try[models_to_try.index(current_model) + 1]
                    logger.warning(
                        "Gemini model '%s' timed out after %.1fs → trying fallback model '%s'",
                        current_model,
                        per_attempt_timeout,
                        next_cand,
                    )
                    continue
                raise

            if resp.status_code in (404, 429, 503) and current_model != models_to_try[-1]:
                next_cand = models_to_try[models_to_try.index(current_model) + 1]
                logger.warning(
                    "Gemini model '%s' returned HTTP %s (quota/unavailable) → trying fallback model '%s'",
                    current_model,
                    resp.status_code,
                    next_cand,
                )
                continue
            break

        if resp is None:
            raise RuntimeError("Gemini failed to return a response")

        resp.raise_for_status()
        data = resp.json()

        try:
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates returned in Gemini response")
            text = candidates[0]["content"]["parts"][0]["text"].strip()
            if not text:
                raise ValueError("Empty candidate text returned by Gemini")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Invalid Gemini response structure: {exc}") from exc

        return CopilotAnswer(
            question=context.question,
            intent=context.intent,
            mode="llm",
            deal_id=context.deal_id,
            deal_name=context.deal_name,
            answer=text,
            items=context.items,
        )


class GroqCopilotProvider:
    name: str = "groq"

    def __init__(
        self, settings: Settings | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings or get_settings()
        self.http_client = http_client
        self.timeout_seconds = self.settings.groq_timeout_seconds

    def is_configured(self) -> bool:
        key = self.settings.groq_api_key
        return key is not None and bool(key.get_secret_value().strip())

    async def answer(self, context: CopilotContext) -> CopilotAnswer:
        if not self.is_configured():
            raise RuntimeError("Groq is not configured")

        key = self.settings.groq_api_key.get_secret_value().strip()  # type: ignore[union-attr]
        model = self.settings.groq_model.strip() or "llama-3.3-70b-versatile"
        url = "https://api.groq.com/openai/v1/chat/completions"

        prompt = build_user_prompt(context.question, context.context_text, context.deal_name)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": COPILOT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)

        if self.http_client:
            resp = await self.http_client.post(url, json=payload, headers=headers, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)

        resp.raise_for_status()
        data = resp.json()

        try:
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("No choices returned in Groq response")
            text = choices[0]["message"]["content"].strip()
            if not text:
                raise ValueError("Empty choice content returned by Groq")
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Invalid Groq response structure: {exc}") from exc

        return CopilotAnswer(
            question=context.question,
            intent=context.intent,
            mode="llm",
            deal_id=context.deal_id,
            deal_name=context.deal_name,
            answer=text,
            items=context.items,
        )


DeterministicFallbackFn = Callable[[CopilotContext], Awaitable[CopilotAnswer]]


class DeterministicCopilotProvider:
    name: str = "deterministic"

    def __init__(self, fallback_fn: DeterministicFallbackFn) -> None:
        self.fallback_fn = fallback_fn

    def is_configured(self) -> bool:
        return True

    async def answer(self, context: CopilotContext) -> CopilotAnswer:
        return await self.fallback_fn(context)


class FallbackCopilotRouter:
    """Orchestrates multi-provider execution with ordered fallback and non-sensitive logging."""

    def __init__(self, providers: list[CopilotProvider]) -> None:
        self.providers = providers

    async def answer(self, context: CopilotContext) -> CopilotAnswer:
        for i, provider in enumerate(self.providers):
            next_provider = self.providers[i + 1].name if i + 1 < len(self.providers) else "none"
            next_desc = (
                f"trying {next_provider.capitalize()}"
                if next_provider != "deterministic"
                else "using deterministic fallback"
            )

            if not provider.is_configured():
                logger.info("%s unavailable → %s", provider.name.capitalize(), next_desc)
                continue

            try:
                logger.info("Copilot provider: %s", provider.name)
                return await provider.answer(context)
            except httpx.TimeoutException:
                timeout_val = getattr(provider, "timeout_seconds", 30.0)
                logger.warning(
                    "%s unavailable (timeout after %ss) → %s",
                    provider.name.capitalize(),
                    timeout_val,
                    next_desc,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                reason = "rate limit" if status_code == 429 else f"HTTP {status_code}"
                logger.warning(
                    "%s unavailable (%s) → %s",
                    provider.name.capitalize(),
                    reason,
                    next_desc,
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "%s unavailable (network error: %s) → %s",
                    provider.name.capitalize(),
                    type(exc).__name__,
                    next_desc,
                )
            except Exception as exc:
                logger.warning(
                    "%s unavailable (invalid response: %s) → %s",
                    provider.name.capitalize(),
                    type(exc).__name__,
                    next_desc,
                )

        raise RuntimeError("All configured Copilot providers failed, including deterministic fallback.")


def create_copilot_router(
    fallback_fn: DeterministicFallbackFn,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FallbackCopilotRouter:
    """Builds the provider chain based on LLM_PROVIDER and COPILOT_PROVIDER_ORDER."""
    s = settings or get_settings()
    order_names = [p.strip().lower() for p in s.copilot_provider_order.split(",") if p.strip()]

    # If LLM_PROVIDER is explicitly configured (e.g. LLM_PROVIDER=gemini), prioritize it
    primary = s.llm_provider.strip().lower()
    if primary in ("gemini", "groq"):
        order_names = [primary] + [p for p in order_names if p != primary]

    provider_map: dict[str, CopilotProvider] = {
        "gemini": GeminiCopilotProvider(settings=s, http_client=http_client),
        "groq": GroqCopilotProvider(settings=s, http_client=http_client),
        "deterministic": DeterministicCopilotProvider(fallback_fn=fallback_fn),
    }

    providers: list[CopilotProvider] = []
    for name in order_names:
        if name in provider_map:
            providers.append(provider_map[name])

    # Ensure deterministic provider is always present at the end as safety net
    if not any(isinstance(p, DeterministicCopilotProvider) for p in providers):
        providers.append(provider_map["deterministic"])

    return FallbackCopilotRouter(providers=providers)
