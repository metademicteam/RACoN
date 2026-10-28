"""Unit tests for chat completions streaming parser."""
import json
import pytest
import httpx

from racon.ui.chat_panel import _chat_stream_request


def test_chat_stream_request_parsing(monkeypatch):
    sse_lines = [
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"!"}}]}\n\n',
        b'data: [DONE]\n\n',
    ]

    class FakeStreamResponse:
        def __init__(self):
            self.status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def iter_lines(self):
            for l in sse_lines:
                yield l.decode("utf-8")

    def fake_stream(*args, **kwargs):
        return FakeStreamResponse()

    monkeypatch.setattr(httpx, "stream", fake_stream)

    received_tokens = []

    def on_token(t):
        received_tokens.append(t)

    content, extra = _chat_stream_request(
        url="http://fake-gateway",
        key="test-key",
        model="gemma-3-1b",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        temperature=0.7,
        on_token=on_token,
    )

    assert content == "Hello world!"
    assert received_tokens == ["Hello", " world", "!"]
    assert extra.get("eval_tokens") == 3
