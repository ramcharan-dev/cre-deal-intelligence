"""Logging setup, secret redaction, and request/response logging middleware.

Log lines carry metadata only (method, path, status, sizes, timings, ids). Request/response bodies,
email content and query strings are never logged, and configured secrets are redacted from every record.
"""

import json
import logging
import re
import time
import traceback
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Provider key and Google OAuth token shapes (access token, refresh token, auth code, client secret), as a
# backstop for credentials that are not in settings (e.g. echoed by an upstream error).
_KEY_PATTERNS = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]{8,}|pa-[A-Za-z0-9_\-]{20,}|Bearer\s+[A-Za-z0-9._\-]{12,}"
    r"|ya29\.[A-Za-z0-9._\-]{10,}|1//[A-Za-z0-9_\-]{20,}|4/[0-9A-Za-z_\-]{20,}|GOCSPX-[A-Za-z0-9_\-]{10,}"
)
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    for secret in get_settings().secret_values():
        text = text.replace(secret, REDACTED)
    return _KEY_PATTERNS.sub(REDACTED, text)


class _ContextFilter(logging.Filter):
    """Adds request_id and redacts secrets from the final message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(_ContextFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Our middleware replaces uvicorn's access log (which would also log query strings).
    logging.getLogger("uvicorn.access").disabled = True
    # HTTP client logs include full URLs (query strings) at INFO and request bodies at DEBUG.
    # The Anthropic SDK 1.x uses httpx2.
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic", "voyage", "cohere"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class RequestLoggingMiddleware:
    """Assigns an X-Request-ID and logs one line per request with status, duration and body sizes.

    It also converts unhandled exceptions into a JSON 500 and logs only their type and stack frames:
    exception messages (e.g. validation errors) can contain email content.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.log = logging.getLogger("app.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if re.fullmatch(r"[A-Za-z0-9\-]{8,64}", incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        status = 500
        request_bytes = 0
        response_bytes = 0

        async def receive_wrapper() -> Message:
            nonlocal request_bytes
            message = await receive()
            if message["type"] == "http.request":
                request_bytes += len(message.get("body", b""))
            return message

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_bytes, started
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            frames = "".join(traceback.format_tb(exc.__traceback__))
            logging.getLogger("app.error").error("unhandled %s\n%s", type(exc).__name__, frames)
            if not started:
                body = json.dumps(
                    {
                        "detail": {
                            "code": "internal_error",
                            "message": "Internal server error",
                            "retryable": False,
                            "email_id": None,
                            "request_id": request_id,
                        }
                    }
                ).encode()
                await send_wrapper(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            level = logging.WARNING if status >= 500 else logging.INFO
            self.log.log(
                level,
                "%s %s -> %s in %.0fms (req %dB, resp %dB)",
                scope["method"],
                scope["path"],  # path only: query strings are not logged
                status,
                duration_ms,
                request_bytes,
                response_bytes,
            )
            request_id_var.reset(token)
