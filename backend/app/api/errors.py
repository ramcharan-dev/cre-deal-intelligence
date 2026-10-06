"""Uniform error responses: every non-2xx body is `{"detail": ErrorDetail}` (422 request validation excepted)."""

import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.crypto import CryptoNotConfiguredError
from app.core.logging import request_id_var
from app.extraction.claude import ErrorCode
from app.services.google_client import GmailError, GmailErrorCode


class ErrorDetail(BaseModel):
    code: str = Field(description="Machine-readable error code", examples=["auth_failed"])
    message: str = Field(
        description="Human-readable explanation. Never contains secrets or email content.",
        examples=["Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY."],
    )
    retryable: bool = Field(False, description="True when retrying the same request may succeed")
    email_id: uuid.UUID | None = Field(
        None, description="Stored email record, when the failure happened after upload"
    )
    request_id: str | None = Field(None, description="Matches the X-Request-ID response header and log lines")


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status_code, {"code": code, "message": message, **extra})


# How extraction failures surface over HTTP.
EXTRACTION_STATUS: dict[ErrorCode, int] = {
    "not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "auth_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
    "permission_denied": status.HTTP_503_SERVICE_UNAVAILABLE,
    "billing": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invalid_request": status.HTTP_502_BAD_GATEWAY,
    "rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
    "overloaded": status.HTTP_503_SERVICE_UNAVAILABLE,
    "upstream_error": status.HTTP_502_BAD_GATEWAY,
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "connection_error": status.HTTP_502_BAD_GATEWAY,
    "refused": getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY),
    "truncated": getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY),
    "invalid_output": getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY),
}


# How Gmail/Google failures surface over HTTP.
GMAIL_STATUS: dict[GmailErrorCode, int] = {
    "gmail_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "gmail_not_connected": status.HTTP_409_CONFLICT,
    "gmail_reauth_required": status.HTTP_409_CONFLICT,
    "gmail_permission_denied": status.HTTP_503_SERVICE_UNAVAILABLE,
    "gmail_rate_limited": status.HTTP_429_TOO_MANY_REQUESTS,
    "gmail_upstream_error": status.HTTP_502_BAD_GATEWAY,
    "gmail_unreachable": status.HTTP_502_BAD_GATEWAY,
    "gmail_timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "oauth_failed": status.HTTP_502_BAD_GATEWAY,
}


def error_responses(*codes: int, **descriptions: str) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries for the given status codes.

    Override a default description with a keyword argument named after the code, e.g. `d409="..."`.
    """
    default = {
        400: "The upload is not a parseable email",
        404: "Not found",
        409: "The email exists but has no extraction result (still processing or failed)",
        413: "Upload exceeds the size limit",
        422: "Claude refused, was truncated, or returned output failing the schema; or the request was malformed",
        429: "Claude rate limit reached (retryable)",
        502: "Claude API error or unreachable (see `code`)",
        503: "Claude not configured, key rejected, out of credits, or overloaded (see `code`)",
        504: "Claude timed out (retryable)",
    }
    return {
        c: {"model": ErrorResponse, "description": descriptions.get(f"d{c}") or default[c]} for c in codes
    }


def install_error_handlers(app: FastAPI) -> None:
    """Unhandled exceptions are turned into a 500 by RequestLoggingMiddleware (see app.core.logging)."""

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = (
            exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        )
        body = ErrorResponse(detail=ErrorDetail(**detail, request_id=request_id_var.get()))
        return JSONResponse(body.model_dump(mode="json"), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(GmailError)
    async def gmail_error(request: Request, exc: GmailError) -> JSONResponse:
        api = ApiError(GMAIL_STATUS[exc.code], exc.code, exc.message, retryable=exc.retryable)
        return await http_error(request, api)

    @app.exception_handler(CryptoNotConfiguredError)
    async def crypto_error(request: Request, exc: CryptoNotConfiguredError) -> JSONResponse:
        api = ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "gmail_not_configured", str(exc))
        return await http_error(request, api)
