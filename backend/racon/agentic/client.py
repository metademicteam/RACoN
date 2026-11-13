"""Minimal async Together AI client.

Uses the OpenAI-compatible ``/chat/completions`` endpoint with ``httpx`` so the server does
not need another SDK. The API key is only ever placed in the request header; it is never
logged or returned in an error body.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from .errors import TogetherError

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _normalise_usage(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    try:
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        total = int(raw.get("total_tokens") or (prompt + completion))
    except (TypeError, ValueError):
        return {}
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "reported": True,
    }


def _retry_delay(response: Optional[httpx.Response], attempt: int) -> float:
    """Honour Retry-After when present, otherwise use a short exponential backoff."""
    if response is not None:
        raw = response.headers.get("Retry-After") or response.headers.get(
            "X-RateLimit-Reset"
        )
        if raw:
            try:
                return max(0.5, min(5.0, float(raw)))
            except (TypeError, ValueError):
                pass
    return min(5.0, 0.75 * (attempt + 1))


def _error_payload(response: httpx.Response) -> tuple[str, str]:
    code = f"http_{response.status_code}"
    message = f"Together AI returned HTTP {response.status_code}"
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        if text:
            message = text[:500]
        return message, code
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or message)[:800]
        code = str(error.get("code") or error.get("type") or code)
    elif isinstance(error, str):
        message = error[:800]
    return message, code


class TogetherClient:
    """Small, testable client for the Together chat-completions API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.together.xyz/v1",
        *,
        timeout_s: float = 90.0,
        max_retries: int = 1,
        client_factory=None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_retries = max(0, int(max_retries))
        self._client_factory = client_factory or httpx.AsyncClient

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_s, connect=min(10.0, self.timeout_s))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _body(
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: Optional[float],
        stop: Optional[list[str]],
        tools: Optional[list[dict[str, Any]]],
        tool_choice: Any,
        response_format: Optional[dict[str, Any]],
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max(1, int(max_tokens)),
            "temperature": float(temperature),
            "stream": stream,
        }
        if top_p is not None:
            body["top_p"] = float(top_p)
        if stop:
            body["stop"] = stop
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice if tool_choice is not None else "auto"
        if response_format:
            body["response_format"] = response_format
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        top_p: Optional[float] = None,
        stop: Optional[list[str]] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """One non-streaming completion.

        Returns ``{"text", "tool_calls", "usage", "finish_reason", "raw"}``.
        """
        if not self.configured:
            raise TogetherError("TOGETHER_API_KEY is not configured", code="not_configured")

        body = self._body(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            stream=False,
        )
        payload: dict[str, Any] = {}
        for attempt in range(self.max_retries + 1):
            payload = await self._post_json("/chat/completions", body)
            if isinstance(payload, dict) and payload.get("error"):
                error = payload.get("error")
                message = (
                    str(error.get("message"))
                    if isinstance(error, dict)
                    else str(error)
                )
                raise TogetherError(message or "Together AI returned an error", code="api_error")
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if choices:
                break
            # A 200 with no choices is how the provider sometimes surfaces throttling.
            if attempt < self.max_retries:
                await asyncio.sleep(max(1.0, _retry_delay(None, attempt)))
                continue
            raise TogetherError(
                "Together AI returned no choices", code="empty_choices", retryable=True
            )

        choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "\n".join(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
        return {
            "text": str(content or ""),
            "tool_calls": message.get("tool_calls") or [],
            "usage": _normalise_usage(payload.get("usage")),
            "finish_reason": choice.get("finish_reason"),
            "raw": payload,
        }

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        top_p: Optional[float] = None,
        stop: Optional[list[str]] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream content deltas and the final usage."""
        if not self.configured:
            raise TogetherError("TOGETHER_API_KEY is not configured", code="not_configured")

        body = self._body(
            messages,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            tools=None,
            tool_choice=None,
            response_format=None,
            stream=True,
        )
        url = f"{self.base_url}/chat/completions"
        last_usage: dict[str, Any] = {}
        finished = False

        for attempt in range(self.max_retries + 1):
            emitted = False
            response: Optional[httpx.Response] = None
            try:
                async with self._client_factory(timeout=self._timeout()) as client:
                    async with client.stream(
                        "POST", url, json=body, headers=self._headers()
                    ) as response:
                        if response.status_code >= 400:
                            await response.aread()
                            message, code = _error_payload(response)
                            error = TogetherError(
                                message,
                                status_code=response.status_code,
                                code=code,
                                retryable=response.status_code in _RETRY_STATUS,
                            )
                            if error.retryable and attempt < self.max_retries:
                                await asyncio.sleep(_retry_delay(response, attempt))
                                continue
                            raise error
                        async for line in response.aiter_lines():
                            line = (line or "").strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                finished = True
                                break
                            try:
                                payload = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            usage = _normalise_usage(payload.get("usage"))
                            if usage:
                                last_usage = usage
                            choices = payload.get("choices") or []
                            if not choices:
                                continue
                            choice = choices[0] or {}
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content:
                                emitted = True
                                yield {"content": str(content)}
                            if delta.get("tool_calls"):
                                emitted = True
                                yield {"tool_calls": delta.get("tool_calls")}
                            if choice.get("finish_reason"):
                                yield {"finish_reason": str(choice["finish_reason"])}
                break
            except httpx.TimeoutException as exc:
                if emitted or attempt >= self.max_retries:
                    raise TogetherError(
                        f"Together AI timed out after {self.timeout_s:.0f}s",
                        code="timeout",
                        retryable=True,
                    ) from exc
                await asyncio.sleep(_retry_delay(None, attempt))
            except httpx.HTTPError as exc:
                raise TogetherError(
                    f"Together AI transport error: {exc}", code="transport"
                ) from exc
            except TogetherError as exc:
                if emitted or not exc.retryable or attempt >= self.max_retries:
                    raise
                await asyncio.sleep(_retry_delay(response, attempt))

        if last_usage:
            yield {"usage": last_usage}
        yield {"done": True, "completed": finished}

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Optional[TogetherError] = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self._client_factory(timeout=self._timeout()) as client:
                    response = await client.post(url, json=body, headers=self._headers())
            except httpx.TimeoutException as exc:
                last_error = TogetherError(
                    f"Together AI timed out after {self.timeout_s:.0f}s",
                    code="timeout",
                    retryable=True,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise last_error from exc
            except httpx.HTTPError as exc:
                raise TogetherError(
                    f"Together AI transport error: {exc}", code="transport"
                ) from exc

            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise TogetherError(
                        "Together AI returned a non-JSON response", code="invalid_response"
                    ) from exc

            message, code = _error_payload(response)
            last_error = TogetherError(
                message,
                status_code=response.status_code,
                code=code,
                retryable=response.status_code in _RETRY_STATUS,
            )
            if last_error.retryable and attempt < self.max_retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise last_error

        raise last_error or TogetherError("Together AI request failed", code="request_failed")
