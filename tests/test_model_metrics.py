"""Observe the real HTTP boundary without network calls or business databases."""
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app import model, model_metrics


CONFIG = {
    'AGRIPLAN_MODEL_BASE_URL': 'https://model.example/v1?secret=private-url-value',
    'AGRIPLAN_MODEL_API_KEY': 'synthetic-private-key',
    'AGRIPLAN_MODEL': 'test-model',
}


def response(content='{}', usage=None):
    body = {'choices': [{'message': {'content': content}}]}
    if usage is not None:
        body['usage'] = usage
    return httpx.Response(200, json=body)


class ModelMetricsTests(unittest.TestCase):
    @contextmanager
    def provider(self, handler):
        real_client = httpx.Client
        transport = httpx.MockTransport(handler)
        with patch.dict(os.environ, CONFIG), patch(
            'app.model.httpx.Client',
            side_effect=lambda **kw: real_client(transport=transport, **kw),
        ):
            yield

    def request(self):
        return model._request_json('private-system-text', {'note': 'private-input-text'})

    def test_second_call_is_blocked_before_http_and_budget_is_not_a_failure(self):
        requests = []
        def handler(request):
            requests.append(request)
            return response(usage={'prompt_tokens': 11, 'completion_tokens': 7})
        with self.provider(handler), model_metrics.record_model_calls(max_calls=1) as recorder:
            self.assertEqual(self.request(), {})
            with self.assertRaises(model_metrics.ModelCallBudgetExceeded):
                self.request()
        summary = recorder.summary()
        self.assertEqual(len(requests), 1)
        self.assertEqual([summary[k] for k in ('attempted_calls', 'successful_calls', 'failed_calls', 'blocked_calls')], [1, 1, 0, 1])
        self.assertEqual((summary['input_tokens'], summary['output_tokens']), (11, 7))
        self.assertTrue(summary['usage_complete'])
        self.assertIsNone(summary['estimated_cost'])
        self.assertEqual(summary['calls'][1]['error_category'], 'call_budget_exceeded')

    def test_usage_only_accepts_nonnegative_integer_provider_values(self):
        fixtures = [
            (None, None, None),
            ({'input_tokens': 0, 'output_tokens': 9}, 0, 9),
            ({'prompt_tokens': 4, 'completion_tokens': 2}, 4, 2),
            ({'input_tokens': True, 'output_tokens': 2}, None, 2),
            ({'input_tokens': -1, 'output_tokens': '3'}, None, None),
            ({'input_tokens': 1.0, 'output_tokens': 2.0}, None, None),
            (['unexpected'], None, None),
        ]
        for usage, expected_input, expected_output in fixtures:
            with self.subTest(usage=usage):
                with self.provider(lambda _: response(usage=usage)), model_metrics.record_model_calls() as recorder:
                    self.request()
                summary = recorder.summary()
                self.assertEqual(summary['input_tokens'], expected_input)
                self.assertEqual(summary['output_tokens'], expected_output)
                self.assertEqual(summary['usage_complete'], expected_input is not None and expected_output is not None)

    def test_missing_usage_does_not_turn_partial_totals_into_complete_usage(self):
        replies = iter([response(usage={'input_tokens': 8, 'output_tokens': 3}), response()])
        with self.provider(lambda _: next(replies)), model_metrics.record_model_calls() as recorder:
            self.request()
            self.request()
        summary = recorder.summary()
        self.assertIsNone(summary['input_tokens'])
        self.assertIsNone(summary['output_tokens'])
        self.assertFalse(summary['usage_complete'])
        self.assertEqual(summary['calls'][0]['input_tokens'], 8)

    def test_http_and_json_failures_are_recorded_without_provider_secrets(self):
        fixtures = [
            (lambda _: httpx.Response(403, text='private-provider-error-body'), httpx.HTTPStatusError, 'http_error', 403),
            (lambda _: httpx.Response(200, text='not-json-private-provider'), json.JSONDecodeError, 'response_json_error', 200),
            (lambda _: response('not-json-private-content'), json.JSONDecodeError, 'response_json_error', 200),
            (lambda _: httpx.Response(200, json={'choices': []}), IndexError, 'invalid_response', 200),
        ]
        for handler, error, category, status in fixtures:
            with self.subTest(category=category):
                with self.provider(handler), model_metrics.record_model_calls() as recorder:
                    with self.assertRaises(error):
                        self.request()
                summary = recorder.summary()
                self.assertEqual((summary['attempted_calls'], summary['failed_calls']), (1, 1))
                self.assertEqual(summary['calls'][0]['http_status'], status)
                self.assertEqual(summary['calls'][0]['error_category'], category)
                serialized = json.dumps(summary)
                for private in ('private', 'model.example', 'Authorization', 'messages'):
                    self.assertNotIn(private, serialized)

    def test_transport_timeout_has_safe_category_and_no_http_status(self):
        def handler(request):
            raise httpx.ReadTimeout('private-timeout-message', request=request)
        with self.provider(handler), model_metrics.record_model_calls() as recorder:
            with self.assertRaises(httpx.ReadTimeout):
                self.request()
        call = recorder.summary()['calls'][0]
        self.assertEqual(call['error_category'], 'timeout')
        self.assertIsNone(call['http_status'])
        self.assertNotIn('private', json.dumps(recorder.summary()))

    def test_elapsed_budget_blocks_and_caps_request_timeout(self):
        clock = [10.0]
        timeouts = []
        def handler(request):
            timeouts.append(request.extensions['timeout'])
            return response()
        with patch('app.model_metrics.time.monotonic', side_effect=lambda: clock[0]), self.provider(handler):
            with model_metrics.record_model_calls(max_seconds=5) as recorder:
                clock[0] = 12.0
                self.request()
                clock[0] = 15.0
                with self.assertRaises(model_metrics.ModelCallBudgetExceeded):
                    self.request()
            summary = recorder.summary()
            clock[0] = 100.0
            self.assertEqual(recorder.summary()['elapsed_ms'], 5000)
        self.assertEqual(timeouts, [{'connect': 3.0, 'read': 3.0, 'write': 3.0, 'pool': 3.0}])
        self.assertEqual(summary['calls'][1]['error_category'], 'time_budget_exceeded')

    def test_nested_contexts_restore_after_error_and_exit_stops_recording(self):
        timeouts = []
        def handler(request):
            timeouts.append(request.extensions['timeout']['read'])
            return response()
        with self.provider(handler):
            with model_metrics.record_model_calls() as outer:
                self.request()
                with self.assertRaisesRegex(RuntimeError, 'test-exit'):
                    with model_metrics.record_model_calls() as inner:
                        self.request()
                        raise RuntimeError('test-exit')
                self.request()
            self.request()
        self.assertEqual(outer.summary()['attempted_calls'], 2)
        self.assertEqual(inner.summary()['attempted_calls'], 1)
        self.assertEqual(timeouts[-1], 40)

    def test_all_operations_record_transport_success_before_schema_validation(self):
        with self.provider(lambda _: response()), model_metrics.record_model_calls() as recorder:
            for invoke in (lambda: model.choose_action({}, ['stop']), lambda: model.generate_candidate({}), lambda: model.generate_discussion({})):
                with self.assertRaises(ValidationError):
                    invoke()
        summary = recorder.summary()
        self.assertEqual(summary['successful_calls'], 3)
        self.assertEqual(summary['failed_calls'], 0)
        self.assertEqual([c['operation'] for c in summary['calls']], ['planner', 'candidate', 'discussion'])

    def test_invalid_configuration_does_not_count_as_transport_attempt(self):
        with patch.dict(os.environ, CONFIG | {'AGRIPLAN_MODEL_API_KEY': ''}), model_metrics.record_model_calls() as recorder:
            with self.assertRaises(ValueError):
                self.request()
        self.assertEqual(recorder.summary()['attempted_calls'], 0)
        self.assertIsNone(recorder.summary()['input_tokens'])
        self.assertFalse(recorder.summary()['usage_complete'])

    def test_invalid_budgets_fail_without_entering_recording_context(self):
        for options in ({'max_calls': 0}, {'max_calls': True}, {'max_calls': 1.5}, {'max_seconds': 0}, {'max_seconds': True}, {'max_seconds': float('inf')}, {'max_seconds': float('nan')}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                with model_metrics.record_model_calls(**options):
                    self.fail('invalid budget entered the context')


if __name__ == '__main__':
    unittest.main()
