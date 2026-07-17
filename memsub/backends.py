"""Swappable LLM backends behind one interface.

Priority order for a real backend ("auto"):
  1. AnthropicAPIBackend  - used when ANTHROPIC_API_KEY is set (explicit opt-in
     to cloud spend). Prefers a haiku-tier model to control cost.
  2. OllamaBackend        - local, zero-cloud: used when an ollama daemon is
     reachable at localhost:11434 (or $OLLAMA_HOST) and no API key is set.
     This is the flagship path for the local-model positioning.
  3. ClaudeCLIBackend     - uses the locally installed `claude` CLI in headless
     mode (`claude -p --output-format json`) when neither of the above applies.
  4. MockBackend          - deterministic scripted responses, for tests and for
     dry runs with zero LLM cost.

Every backend exposes complete(system, messages) -> str. Messages are a list of
{"role": "user"|"assistant", "content": str}. Callers log every call.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class LLMCall:
    system: str
    messages: list[dict[str, str]]
    response: str
    backend: str
    model: str


class LLMBackend(Protocol):
    name: str
    model: str

    def complete(self, system: str, messages: list[dict[str, str]]) -> str: ...


# --------------------------------------------------------------------------- #
# Mock backend
# --------------------------------------------------------------------------- #
class MockBackend:
    """Deterministic backend driven by a responder callable.

    The responder receives (system, messages) and returns the response string.
    Defaults to an echo-ish stub. Tests install role-specific responders.
    """

    name = "mock"

    def __init__(
        self,
        responder: Callable[[str, list[dict[str, str]]], str] | None = None,
        model: str = "mock",
    ) -> None:
        self.model = model
        self._responder = responder or (lambda s, m: "OK")
        self.calls: list[LLMCall] = []

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        resp = self._responder(system, messages)
        self.calls.append(LLMCall(system, messages, resp, self.name, self.model))
        return resp


# --------------------------------------------------------------------------- #
# Claude CLI backend (headless)
# --------------------------------------------------------------------------- #
class ClaudeCLIBackend:
    """Calls the local `claude` CLI in headless mode.

    Uses `claude -p --output-format json --model <model>`. The full prompt is
    passed on stdin; system instructions are prepended as a framed block since
    headless mode takes a single prompt string.
    """

    name = "claude_cli"

    def __init__(self, model: str = "haiku", timeout: int = 180) -> None:
        if shutil.which("claude") is None:
            raise RuntimeError("claude CLI not found on PATH")
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        prompt = self._render_prompt(system, messages)
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--model", self.model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {proc.stderr[:400]}")
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout.strip()
        return str(data.get("result", "")).strip()

    @staticmethod
    def _render_prompt(system: str, messages: list[dict[str, str]]) -> str:
        parts = [f"<system_instructions>\n{system}\n</system_instructions>"]
        for m in messages:
            role = m["role"].upper()
            parts.append(f"<{role}>\n{m['content']}\n</{role}>")
        parts.append(
            "Respond now as the assistant. Output only the response content, no preamble."
        )
        return "\n\n".join(parts)


def strip_think_blocks(text: str) -> str:
    """Remove reasoning blocks some local models (qwen3) emit before content.

    Handles well-formed <think>...</think> spans, and an unterminated
    leading <think> (truncated generations) by dropping everything up to
    the last </think> or, absent one, treating the whole string as reasoning
    only if nothing follows a stray opening tag.
    """
    cleaned = _THINK_RE.sub("", text)
    if "<think>" in cleaned:
        # unterminated block: drop from the tag to the end
        cleaned = cleaned.split("<think>", 1)[0]
    return cleaned.strip()


def ollama_available(host: str = OLLAMA_HOST, timeout: float = 0.5) -> bool:
    """True if an ollama daemon answers at host. Cheap, short-timeout probe."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


# --------------------------------------------------------------------------- #
# Ollama backend (local, zero-cloud)
# --------------------------------------------------------------------------- #
class OllamaBackend:
    """Calls a local ollama daemon's /api/chat endpoint.

    Default model is qwen3:8b (fast, good enough for extraction/consolidation
    JSON tasks). Pass model="deepseek-r1:32b" for higher-quality judgment
    calls when latency is less important. qwen3 wraps its answer in
    <think>...</think> reasoning before the real content; strip_think_blocks
    removes that so callers get clean JSON to parse.
    """

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen3:8b",
        host: str = OLLAMA_HOST,
        timeout: float = 180.0,
        think: bool | None = None,
    ) -> None:
        self.model = model
        self.host = host
        self.timeout = timeout
        # None (default): omit the key, unchanged behavior for every existing
        # caller. False: ollama >=0.9 skips qwen3's <think> reasoning pass,
        # ~25x faster on this machine (35s -> 1.3s for a trivial call) at the
        # cost of the reasoning trace; used for bounded, structured-output
        # judgment calls (fusion, decomposition) where a short direct answer
        # is enough. Callers that want the reasoning trace pass think=True.
        self.think = think

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        if self.think is not None:
            payload["think"] = self.think
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A timeout during connect is wrapped in URLError, but a timeout
            # during resp.read() (the slow part: ollama is still generating)
            # raises a raw socket.timeout, which is TimeoutError since
            # Python 3.10 -- urllib does not wrap it. That escaped uncaught
            # in production (~/.memsub/daily_extract.log, 2026-07-14 12:39).
            # OSError is included too (judgment call): it is TimeoutError's
            # and ConnectionError's common base, and any other low-level
            # socket failure during read() would be just as uncaught and
            # just as much "the ollama request failed" from the caller's
            # perspective. URLError is not itself an OSError subclass, so
            # this does not create any duplicate handling.
            raise RuntimeError(f"ollama request failed: {exc}") from exc
        content = str(data.get("message", {}).get("content", ""))
        return strip_think_blocks(content)


# --------------------------------------------------------------------------- #
# Anthropic API backend
# --------------------------------------------------------------------------- #
class AnthropicAPIBackend:
    """Uses the Anthropic Python SDK. Prefers haiku-tier for cost control."""

    name = "anthropic_api"

    def __init__(self, model: str = "claude-haiku-4-5", max_tokens: int = 1024) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("anthropic SDK not installed") from exc
        import anthropic

        self._client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_backend(prefer: str = "auto", model: str | None = None) -> LLMBackend:
    """Select a backend.

    prefer:
      "auto"        - API if ANTHROPIC_API_KEY set; else ollama if reachable
                      (zero-cloud, the local-model story); else claude CLI;
                      else error.
      "api"         - force Anthropic API.
      "ollama"      - force ollama (raises if unreachable).
      "cli"         - force claude CLI.
      "mock"        - force mock (no responder; returns "OK").
    """
    if prefer == "mock":
        return MockBackend(model=model or "mock")
    if prefer == "api" or (prefer == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicAPIBackend(model=model or "claude-haiku-4-5")
    if prefer == "ollama" or (prefer == "auto" and ollama_available()):
        if not ollama_available():
            raise RuntimeError(f"ollama not reachable at {OLLAMA_HOST}")
        return OllamaBackend(model=model or "qwen3:8b")
    if prefer == "cli" or prefer == "auto":
        if shutil.which("claude") is not None:
            return ClaudeCLIBackend(model=model or "haiku")
    raise RuntimeError(
        "no LLM backend available: set ANTHROPIC_API_KEY, run ollama, install "
        "the claude CLI, or use prefer='mock'"
    )
