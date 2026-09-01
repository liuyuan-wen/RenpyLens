# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path

import httpx


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from translator import BuiltinTranslator, KeyExpiredError, RateLimitError


def make_response(message=None, retry_after="60"):
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    content = {"error": {"message": message}} if message is not None else {"error": {}}
    return httpx.Response(
        429,
        headers=headers,
        json=content,
        request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
    )


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.stream_calls = 0
        self.is_closed = False

    def post(self, *args, **kwargs):
        self.calls += 1
        return self.response

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response

    def stream(self, *args, **kwargs):
        self.stream_calls += 1
        return FakeStreamContext(self.response)

    def close(self):
        self.is_closed = True


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class RateLimitErrorTests(unittest.TestCase):
    def test_token_limit_message_uses_server_values(self):
        response = make_response(
            "Rate limit exceeded. Limit type: tokens. Current limit: 5000, Remaining: 0."
        )

        error = RateLimitError.from_response(response)

        self.assertEqual(error.limit_type, "tokens")
        self.assertEqual(error.current_limit, 5000)
        self.assertEqual(error.retry_after, 60)
        self.assertEqual(
            str(error),
            "本分钟翻译内容已达到 5000 Token 上限，请等待约 60 秒后再试。"
            "全量翻译时可调小每批翻译条数。",
        )

    def test_request_and_parallel_limit_messages(self):
        cases = [
            (
                "requests",
                60,
                "请求过于频繁，已达到每分钟 60 次上限，请等待约 60 秒后再试。",
            ),
            (
                "max_parallel_requests",
                3,
                "同时进行的翻译请求过多，当前最多允许 3 个，请等待约 60 秒后再试。",
            ),
        ]
        for limit_type, limit, expected in cases:
            with self.subTest(limit_type=limit_type):
                response = make_response(
                    f"Rate limit exceeded. Limit type: {limit_type}. Current limit: {limit}."
                )
                self.assertEqual(str(RateLimitError.from_response(response)), expected)

    def test_unknown_or_malformed_response_uses_safe_fallback(self):
        response = make_response(message=None, retry_after="not-a-number")

        error = RateLimitError.from_response(response)

        self.assertEqual(str(error), "API 请求过于频繁，请稍后再试。")
        self.assertNotIn("example.test", str(error))

    def test_non_streaming_429_is_not_retried(self):
        translator = BuiltinTranslator({"builtin_url": "https://example.test"})
        client = FakeClient(
            make_response("Limit type: requests. Current limit: 60.")
        )
        translator._client = client

        with self.assertRaises(RateLimitError):
            translator._call_api("system", "user")

        self.assertEqual(client.calls, 1)

    def test_builtin_latency_accepts_an_http_response_without_authentication(self):
        response = httpx.Response(
            401,
            request=httpx.Request("GET", "https://example.test/v1/models"),
        )
        translator = BuiltinTranslator({"builtin_url": "https://example.test"})
        client = FakeClient(response)
        translator._client = client

        latency_ms, status_code = translator.test_latency()

        self.assertGreaterEqual(latency_ms, 0)
        self.assertEqual(status_code, 401)
        self.assertEqual(client.calls, 1)

    def test_streaming_429_uses_same_error(self):
        translator = BuiltinTranslator({"builtin_url": "https://example.test"})
        client = FakeClient(
            make_response("Limit type: max_parallel_requests. Current limit: 3.")
        )
        translator._client = client

        with self.assertRaisesRegex(RateLimitError, "当前最多允许 3 个"):
            translator.call_api_streaming("system", "user")

        self.assertEqual(client.stream_calls, 1)

    def test_success_permission_and_expired_key_behaviors_are_unchanged(self):
        cases = [
            (
                httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "OK"}}], "usage": {}},
                    request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                ),
                None,
            ),
            (
                httpx.Response(
                    403,
                    request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                ),
                PermissionError,
            ),
            (
                httpx.Response(
                    400,
                    json={"error": {"type": "expired_key"}},
                    request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
                ),
                KeyExpiredError,
            ),
        ]

        for response, expected_error in cases:
            with self.subTest(status=response.status_code):
                translator = BuiltinTranslator({"builtin_url": "https://example.test"})
                client = FakeClient(response)
                translator._client = client
                if expected_error is None:
                    self.assertEqual(translator._call_api("system", "user"), "OK")
                else:
                    with self.assertRaises(expected_error):
                        translator._call_api("system", "user")
                self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
