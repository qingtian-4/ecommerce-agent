"""Small recovery decisions. No operation is replayed by this module."""
from __future__ import annotations

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from openai import APIConnectionError, APITimeoutError

from .context import ContextLimitExceeded
from .runtime import BudgetExceeded

WRITES = {'update_return_task', 'cancel_return_task', 'preview_return'}


class IncompleteResponse(RuntimeError):
    pass


def classify_failure(*, exc: Exception | None = None, payload=None,
                     write: bool = False) -> dict:
    category, retryable = 'internal_error', False
    if exc is not None:
        status = getattr(exc, 'status_code', None)
        if isinstance(exc, BudgetExceeded):
            category = 'budget_exceeded'
        elif isinstance(exc, ContextLimitExceeded):
            category = 'context_limit'
        elif status == 429:
            category, retryable = 'rate_limit', True
        elif status is not None and 500 <= status < 600:
            category, retryable = 'service_unavailable', True
        elif isinstance(exc, (TimeoutError, APITimeoutError, httpx.TimeoutException)):
            category, retryable = 'timeout', True
        elif isinstance(exc, (ConnectionError, APIConnectionError, httpx.NetworkError)):
            category, retryable = 'connection', True
        elif isinstance(exc, IncompleteResponse):
            category = 'invalid_response'
        elif status is not None:
            category = 'provider_rejected'
    elif isinstance(payload, dict):
        kind = payload.get('error_type')
        category = {'forbidden': 'permission_denied', 'route_scope': 'route_scope',
                    'invalid_arguments': 'invalid_arguments',
                    'business_rejected': 'business_rejected',
                    'tool_exception': 'tool_failure'}.get(kind, kind or 'ok')
        if category == 'ok' and payload.get('found') is False:
            category = 'not_found'
        if 'orders' in payload and (not isinstance(payload['orders'], list)
                                   or type(payload.get('count')) is not int
                                   or payload['count'] != len(payload['orders'])):
            category = 'invalid_result'
        if category == 'ok' and 'error' in payload:
            category = 'tool_failure'
    else:
        category = 'invalid_result'
    return {'category': category, 'retryable': retryable and not write,
            'side_effect': 'possible' if write else 'none',
            'action': ('reconcile' if write else 'retry_before_content' if retryable
                       else 'clarify' if category in {'invalid_arguments', 'not_found'}
                       else 'stop_or_answer_with_evidence')}


def retry_delay(exc: Exception, attempt: int) -> float:
    """Respect seconds or HTTP date Retry-After; caller enforces the whole-turn deadline."""
    value = getattr(getattr(exc, 'response', None), 'headers', {}).get('Retry-After')
    delay = .2 * 2 ** attempt + random.uniform(0, .05)
    if value is not None:
        try:
            requested = float(value)
        except (TypeError, ValueError):
            try:
                requested = (parsedate_to_datetime(str(value)) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        delay = max(delay, requested)
    return delay
