"""Typed application errors with stable machine-readable codes.

Every error that reaches the API boundary is one of these, so the extension can
show a specific message instead of a generic failure.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Sanitized operator-facing detail (provider status, quota id, ...). Logged and
        # recorded by evaluation scripts; never returned to API clients.
        self.diagnostics = diagnostics


class MissingCredentialsError(AppError):
    code = "missing_credentials"
    status_code = 503


class ModelNotFoundError(AppError):
    code = "model_not_found"
    status_code = 500


class VideoUnavailableError(AppError):
    code = "video_unavailable"
    status_code = 422


class InvalidInputError(AppError):
    code = "invalid_input"
    status_code = 422


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    retryable = True


class ProviderRateLimitedError(AppError):
    code = "provider_rate_limited"
    status_code = 429
    retryable = True


class ProviderQuotaExhaustedError(AppError):
    """The key's quota (e.g. a free-tier daily request cap) is used up; retrying soon won't help."""

    code = "provider_quota_exhausted"
    status_code = 429
    retryable = False


class ProviderTimeoutError(AppError):
    code = "provider_timeout"
    status_code = 504
    retryable = True


class ProviderError(AppError):
    code = "provider_error"
    status_code = 502
    retryable = True


class RetrievalUnavailableError(AppError):
    """No evidence could be retrieved at all (every search or page fetch failed).

    Distinct from the `insufficient_evidence` verdict, which means retrieval worked
    and the evidence found did not settle the claim.
    """

    code = "retrieval_unavailable"
    status_code = 503
    retryable = True


class ConfigurationError(AppError):
    """The backend is configured inconsistently (e.g. a paid provider without a budget)."""

    code = "backend_misconfigured"
    status_code = 503


class InvalidModelOutputError(AppError):
    code = "invalid_model_output"
    status_code = 502
    retryable = True
