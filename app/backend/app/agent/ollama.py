"""Minimal Ollama client (local, no cloud). Every failure surfaces as OllamaUnavailable.

Every call carries an explicit connect/read timeout so a stalled daemon can never hang a run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from . import constants as C

logger = logging.getLogger(__name__)

_FAILS = (httpx.HTTPError, ValueError, KeyError, TypeError)


class OllamaUnavailable(RuntimeError):
    """Ollama unreachable, timed out, or answered with an error / unexpected body."""


def _timeout(read_s: float) -> httpx.Timeout:
    return httpx.Timeout(connect=C.OLLAMA_CONNECT_TIMEOUT_S, read=read_s, write=read_s, pool=read_s)


class OllamaClient:
    """Thin wrapper over an injected httpx.Client (base_url = Ollama), so tests can mock transport."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def models(self) -> dict[str, str]:
        """Installed models as {name: digest} via /api/tags.

        Raises:
            OllamaUnavailable: if the daemon cannot be reached or the body is malformed.
        """
        try:
            resp = self._http.get("/api/tags", timeout=_timeout(C.OLLAMA_PROBE_TIMEOUT_S))
            resp.raise_for_status()
            return {str(m["name"]): str(m["digest"]) for m in resp.json()["models"]}
        except _FAILS as exc:
            raise OllamaUnavailable(f"tags: {type(exc).__name__}") from exc

    def loaded(self) -> list[str]:
        """Names of models currently resident in memory (/api/ps).

        Raises:
            OllamaUnavailable: if the daemon cannot be reached or the body is malformed.
        """
        try:
            resp = self._http.get("/api/ps", timeout=_timeout(C.OLLAMA_PROBE_TIMEOUT_S))
            resp.raise_for_status()
            return [str(m["name"]) for m in resp.json().get("models", [])]
        except _FAILS as exc:
            raise OllamaUnavailable(f"ps: {type(exc).__name__}") from exc

    def warm_up(self, model: str, keep_alive: str = C.KEEP_ALIVE) -> float:
        """Load `model` into memory (empty generate) and pin it for `keep_alive`; returns load ms.

        Raises:
            OllamaUnavailable: on transport errors or a non-2xx reply (e.g. model not pulled).
        """
        try:
            resp = self._http.post("/api/generate", json={"model": model, "keep_alive": keep_alive},
                                   timeout=_timeout(C.OLLAMA_WARMUP_TIMEOUT_S))
            resp.raise_for_status()
            body = resp.json()
        except _FAILS as exc:
            raise OllamaUnavailable(f"warm-up {model}: {type(exc).__name__}") from exc
        return float(body.get("load_duration", 0)) / 1e6

    def _body(self, model: str, messages: list[dict[str, Any]], num_predict: int,
              fmt: dict[str, Any] | None, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model, "messages": messages, "stream": stream, "keep_alive": C.KEEP_ALIVE,
            "options": {"temperature": C.LLM_TEMPERATURE, "seed": C.LLM_SEED,
                        "num_ctx": C.LLM_NUM_CTX, "num_predict": num_predict},
        }
        if fmt is not None:
            body["format"] = fmt
        return body

    def chat(
        self, model: str, messages: list[dict[str, Any]], *, num_predict: int,
        fmt: dict[str, Any] | None = None, timeout_s: float = C.OLLAMA_CHAT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """One non-streaming /api/chat call (temperature 0, fixed seed); returns the message.

        `fmt` is an optional JSON schema: Ollama then constrains decoding to it (structured output).

        Raises:
            OllamaUnavailable: on transport errors, timeout, non-2xx, or a body without `message`.
        """
        try:
            resp = self._http.post("/api/chat", json=self._body(model, messages, num_predict, fmt, False),
                                   timeout=_timeout(timeout_s))
            resp.raise_for_status()
            msg = resp.json()["message"]
        except _FAILS as exc:
            raise OllamaUnavailable(f"chat: {type(exc).__name__}") from exc
        if not isinstance(msg, dict):
            raise OllamaUnavailable("chat: message is not an object")
        return msg

    def chat_stream(
        self, model: str, messages: list[dict[str, Any]], *, num_predict: int,
        timeout_s: float = C.OLLAMA_CHAT_TIMEOUT_S, on_delta: Callable[[str], None] | None = None,
    ) -> str:
        """Streaming /api/chat: calls `on_delta` per token chunk and returns the full content.

        Raises:
            OllamaUnavailable: on transport errors, timeout, non-2xx, or malformed stream lines.
        """
        parts: list[str] = []
        try:
            with self._http.stream("POST", "/api/chat", json=self._body(model, messages, num_predict, None, True),
                                   timeout=_timeout(timeout_s)) as resp:
                resp.raise_for_status()
                for line in self._lines(resp):
                    chunk = json.loads(line)
                    delta = str(chunk.get("message", {}).get("content", ""))
                    if delta:
                        parts.append(delta)
                        if on_delta is not None:
                            on_delta(delta)
                    if chunk.get("done"):
                        break
        except (*_FAILS, AttributeError) as exc:
            raise OllamaUnavailable(f"chat stream: {type(exc).__name__}") from exc
        return "".join(parts)

    @staticmethod
    def _lines(resp: httpx.Response) -> Iterator[str]:
        for line in resp.iter_lines():
            if line.strip():
                yield line
