import json
import logging
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx


class _FakeAnalysis:
    pass


class _FakeOpenAI:
    def __init__(self, *, base_url: str, api_key: str, http_client: httpx.Client):
        self.base_url = base_url
        self.api_key = api_key
        self.http_client = http_client
        self.chat = _FakeChat(self)


class _FakeChat:
    def __init__(self, client: _FakeOpenAI):
        self.completions = _FakeCompletions(client)


class _FakeCompletions:
    def __init__(self, client: _FakeOpenAI):
        self._client = client

    def create(self, *, model, messages, **kwargs):
        response = self._client.http_client.post(
            "/chat/completions",
            headers={"Authorization": f"Bearer {self._client.api_key}"},
            json={"model": model, "messages": messages, **kwargs},
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
sys.modules.setdefault("models", types.SimpleNamespace(Analysis=_FakeAnalysis))

import ai.analyzer as analyzer


class AnalyzerClientAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        analyzer._client = None
        self.addCleanup(self._reset_client)

    def _reset_client(self) -> None:
        analyzer._client = None

    def test_get_client_requires_ai_api_token(self) -> None:
        original_token = analyzer.settings.AI_API_TOKEN
        analyzer.settings.AI_API_TOKEN = ""
        self.addCleanup(setattr, analyzer.settings, "AI_API_TOKEN", original_token)

        with self.assertRaisesRegex(RuntimeError, "AI_API_TOKEN is not set"):
            analyzer.get_client()

    def test_openai_client_sends_bearer_token_without_logging_secret(self) -> None:
        captured = {}
        token = "super-secret-test-token"
        original_httpx_client = analyzer.httpx.Client
        original_base_url = analyzer.settings.AI_BASE_URL
        original_model = analyzer.settings.AI_MODEL
        original_token = analyzer.settings.AI_API_TOKEN

        analyzer.settings.AI_BASE_URL = "https://ai.example.test/v1"
        analyzer.settings.AI_MODEL = "llama3"
        analyzer.settings.AI_API_TOKEN = token

        self.addCleanup(setattr, analyzer.settings, "AI_BASE_URL", original_base_url)
        self.addCleanup(setattr, analyzer.settings, "AI_MODEL", original_model)
        self.addCleanup(setattr, analyzer.settings, "AI_API_TOKEN", original_token)

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("Authorization")
            captured["path"] = request.url.path
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "llama3",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        def build_http_client(*args, **kwargs) -> httpx.Client:
            transport = httpx.MockTransport(handler)
            return original_httpx_client(
                transport=transport,
                base_url=analyzer.settings.AI_BASE_URL,
            )

        with patch.object(analyzer.httpx, "Client", side_effect=build_http_client):
            with self.assertLogs(analyzer.logger, level=logging.INFO) as logs:
                client = analyzer.get_client()
                response = client.chat.completions.create(
                    model=analyzer.settings.AI_MODEL,
                    messages=[{"role": "user", "content": "hello"}],
                )

        self.assertEqual(response.choices[0].message.content, "ok")
        self.assertEqual(captured["authorization"], f"Bearer {token}")
        self.assertEqual(captured["path"], "/v1/chat/completions")
        rendered_logs = "\n".join(logs.output)
        self.assertNotIn(token, rendered_logs)
        self.assertNotIn(json.dumps({"token": token}), rendered_logs)


if __name__ == "__main__":
    unittest.main()
