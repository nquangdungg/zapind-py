from __future__ import annotations

from typing import Any


class ZapindError(Exception):
    """Base exception for all Zapind SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "ZAPIND_ERROR",
        retryable: bool = False,
        raw_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.raw_code = raw_code
        self.cause = cause


class AuthenticationError(ZapindError):
    def __init__(
        self,
        message: str = "Not authenticated with Zalo",
        *,
        raw_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code="AUTHENTICATION_ERROR",
            retryable=False,
            raw_code=raw_code,
            cause=cause,
        )


class SessionError(ZapindError):
    def __init__(
        self,
        message: str = "Session is invalid, expired, or could not be accessed",
        *,
        raw_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code="SESSION_ERROR",
            retryable=False,
            raw_code=raw_code,
            cause=cause,
        )


class NetworkError(ZapindError):
    def __init__(
        self,
        message: str = "Network error communicating with Zalo",
        *,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, code="NETWORK_ERROR", retryable=True, cause=cause)


class RateLimitError(ZapindError):
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after_ms: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, code="RATE_LIMIT_ERROR", retryable=True, cause=cause)
        self.retry_after_ms = retry_after_ms


class ValidationError(ZapindError):
    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR", retryable=False, cause=cause)


class PermissionError(ZapindError):
    def __init__(
        self,
        message: str = "Insufficient permissions for this operation",
        *,
        raw_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code="PERMISSION_ERROR",
            retryable=False,
            raw_code=raw_code,
            cause=cause,
        )


class NotFoundError(ZapindError):
    def __init__(
        self,
        message: str = "Requested resource was not found",
        *,
        raw_code: int | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code="NOT_FOUND_ERROR",
            retryable=False,
            raw_code=raw_code,
            cause=cause,
        )


class APIError(ZapindError):
    def __init__(
        self,
        message: str,
        *,
        raw_code: int | None = None,
        retryable: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(
            message,
            code="API_ERROR",
            retryable=retryable,
            raw_code=raw_code,
            cause=cause,
        )


def normalize_error(
    err: Any,
    fallback_message: str = "Unexpected Zalo API error",
) -> ZapindError:
    """Normalize any transport or runtime error into a typed ZapindError."""
    if isinstance(err, ZapindError):
        return err

    cause = err if isinstance(err, BaseException) else None
    if isinstance(err, dict):
        message = str(err.get("message") or fallback_message)
        status = err.get("status")
        raw_code = err.get("code") if isinstance(err.get("code"), int) else None
        retry_after = err.get("retry_after_ms") or err.get("retryAfterMs")
        err_code = str(err.get("code") or "")
    else:
        message = str(err) if str(err).strip() else fallback_message
        status = getattr(err, "status", None)
        raw_code = getattr(err, "raw_code", None)
        retry_after = getattr(err, "retry_after_ms", None)
        err_code = str(getattr(err, "code", "") or "")

    lower = message.lower()

    if err_code == "RATE_LIMIT_ERROR" or status == 429 or "rate limit" in lower or "too many requests" in lower:
        return RateLimitError(
            message,
            retry_after_ms=int(retry_after) if isinstance(retry_after, (int, float)) else None,
            cause=cause,
        )
    if (
        err_code == "SESSION_ERROR"
        or status == 401
        or "session expired" in lower
        or "invalid cookie" in lower
        or "unauthorized" in lower
    ):
        return SessionError(message, raw_code=raw_code, cause=cause)
    if err_code == "AUTHENTICATION_ERROR" or "not authenticated" in lower or "qr" in lower and "declined" in lower:
        return AuthenticationError(message, raw_code=raw_code, cause=cause)
    if err_code == "PERMISSION_ERROR" or status == 403 or "forbidden" in lower or "permission" in lower:
        return PermissionError(message, raw_code=raw_code, cause=cause)
    if err_code == "NOT_FOUND_ERROR" or status == 404 or "not found" in lower:
        return NotFoundError(message, raw_code=raw_code, cause=cause)
    if err_code == "VALIDATION_ERROR":
        return ValidationError(message, cause=cause)
    if (
        err_code == "NETWORK_ERROR"
        or isinstance(err, (TimeoutError, ConnectionError, OSError))
        or "timed out" in lower
        or "network" in lower
        or "socket hang up" in lower
    ):
        return NetworkError(message, cause=cause)

    retryable = isinstance(status, int) and status >= 500
    return APIError(message, raw_code=raw_code, retryable=retryable, cause=cause)
