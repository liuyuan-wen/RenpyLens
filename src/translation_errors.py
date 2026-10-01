"""Short, actionable messages for translation request failures."""

import json

import httpx

from i18n import tr
from translator import KeyExpiredError, RateLimitError


def describe_translation_error(error: Exception) -> str:
    if isinstance(error, KeyExpiredError):
        return tr("translation.error_key_expired")
    if isinstance(error, RateLimitError):
        return str(error)
    if isinstance(error, httpx.TimeoutException):
        return tr("translation.error_timeout")
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 401:
            return tr("translation.error_auth")
        if status == 402:
            return tr("translation.error_payment")
        if status == 403:
            return tr("translation.error_forbidden")
        if status == 404:
            return tr("translation.error_not_found")
        if status == 429:
            return tr("translation.error_rate_limit")
        if status == 413:
            return tr("translation.error_too_large")
        if status in (408, 504):
            return tr("translation.error_timeout")
        if status in (400, 422):
            return tr("translation.error_request", status=status)
        if status >= 500:
            return tr("translation.error_server", status=status)
        return tr("translation.error_http", status=status)
    if isinstance(error, PermissionError):
        return tr("translation.error_forbidden")
    if isinstance(error, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return tr("translation.error_url")
    if isinstance(error, httpx.DecodingError):
        return tr("translation.error_response")
    if isinstance(error, (httpx.RequestError, ConnectionError)):
        return tr("translation.error_connection")
    if isinstance(error, (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError)):
        return tr("translation.error_response")
    return tr("translation.error_unexpected", kind=type(error).__name__)
