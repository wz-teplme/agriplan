"""Context-local HTTP evidence and budgets; never retain provider text or URLs."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import math
import time

import httpx


class ModelCallBudgetExceeded(RuntimeError):
    """Raised before HTTP dispatch when the local evaluation budget is spent."""


_recorder = ContextVar('model_call_recorder', default=None)
_OPERATIONS = frozenset(('planner', 'candidate', 'discussion'))


def _token_count(usage, standard, alternate):
    if not isinstance(usage, dict):
        return None
    value = usage.get(standard, usage.get(alternate))
    return value if type(value) is int and value >= 0 else None


def _error_category(error):
    if isinstance(error, httpx.HTTPStatusError):
        return 'http_error'
    if isinstance(error, httpx.TimeoutException):
        return 'timeout'
    if isinstance(error, httpx.RequestError):
        return 'transport_error'
    if isinstance(error, json.JSONDecodeError):
        return 'response_json_error'
    if isinstance(error, (KeyError, IndexError, TypeError, ValueError)):
        return 'invalid_response'
    return 'internal_error'


@dataclass
class _ObservedCall:
    timeout: float
    http_status: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def observe_usage(self, envelope):
        usage = envelope.get('usage') if isinstance(envelope, dict) else None
        self.input_tokens = _token_count(usage, 'input_tokens', 'prompt_tokens')
        self.output_tokens = _token_count(usage, 'output_tokens', 'completion_tokens')


class ModelCallRecorder:
    def __init__(self, max_calls, max_seconds):
        if type(max_calls) is not int or max_calls <= 0:
            raise ValueError('max_calls must be a positive integer')
        if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0:
            raise ValueError('max_seconds must be a positive finite number')
        self.max_calls = max_calls
        self.max_seconds = max_seconds
        self._started = time.monotonic()
        self._ended = None
        self._attempted = 0
        self._calls = []

    @contextmanager
    def observe(self, operation):
        # Only controlled operation names may enter report metadata.
        if operation not in _OPERATIONS:
            raise ValueError('unknown model operation')
        started = time.monotonic()
        remaining = self.max_seconds - (started - self._started)
        category = None
        if self._attempted >= self.max_calls:
            category = 'call_budget_exceeded'
        elif remaining <= 0:
            category = 'time_budget_exceeded'
        entry = {
            'operation': operation, 'elapsed_ms': 0, 'http_status': None,
            'status': 'blocked' if category else 'failed', 'error_category': category,
            'input_tokens': None, 'output_tokens': None,
        }
        self._calls.append(entry)
        if category:
            raise ModelCallBudgetExceeded(category)
        self._attempted += 1
        call = _ObservedCall(timeout=min(40, remaining))
        try:
            yield call
        except BaseException as error:
            entry['error_category'] = _error_category(error)
            raise
        else:
            entry['status'] = 'success'
        finally:
            entry.update(
                elapsed_ms=max(0, round((time.monotonic() - started) * 1000, 3)),
                http_status=call.http_status,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
            )

    def summary(self):
        attempted = [call for call in self._calls if call['status'] != 'blocked']
        def total(field):
            if not attempted or any(call[field] is None for call in attempted):
                return None
            return sum(call[field] for call in attempted)
        input_tokens, output_tokens = total('input_tokens'), total('output_tokens')
        ended = time.monotonic() if self._ended is None else self._ended
        return {
            'attempted_calls': self._attempted,
            'successful_calls': sum(call['status'] == 'success' for call in attempted),
            'failed_calls': sum(call['status'] == 'failed' for call in attempted),
            'blocked_calls': len(self._calls) - len(attempted),
            'elapsed_ms': max(0, round((ended - self._started) * 1000, 3)),
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'usage_complete': input_tokens is not None and output_tokens is not None,
            'estimated_cost': None,
            'calls': [dict(call) for call in self._calls],
        }


@contextmanager
def record_model_calls(max_calls=20, max_seconds=120):
    """Record calls in this context; nested scopes restore their parent on exit."""
    recorder = ModelCallRecorder(max_calls, max_seconds)
    token = _recorder.set(recorder)
    try:
        yield recorder
    finally:
        recorder._ended = time.monotonic()
        _recorder.reset(token)


@contextmanager
def observe_model_call(operation):
    """Internal request hook; ordinary requests retain their original timeout."""
    recorder = _recorder.get()
    if recorder is None:
        yield None
    else:
        with recorder.observe(operation) as call:
            yield call
