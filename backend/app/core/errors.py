"""Typed application errors with stable machine-readable codes.

Every error that reaches the API boundary is one of these, so the extension can
show a specific message instead of a generic failure.
"""

from __future__ import annotations


class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


class ProviderTimeoutError(AppError):
    code = "provider_timeout"
    status_code = 504
    retryable = True


class ProviderError(AppError):
    code = "provider_error"
    status_code = 502
    retryable = True


class InvalidModelOutputError(AppError):
    code = "invalid_model_output"
    status_code = 502
    retryable = True
