"""Swappable LLM backends behind one interface.

Priority order for a real backend:
  1. AnthropicAPIBackend  - used when ANTHROPIC_API_KEY is set. Prefers a
     haiku-tier model for high-volume experiment calls to control cost.
  2. ClaudeCLIBackend     - uses the locally installed `claude` CLI in headless
     mode (`claude -p --output-format json`) when no API key is present.
  3. MockBackend          - deterministic scripted responses, for tests and for
     dry runs with zero LLM cost.

Every backend exposes complete(system, messages) -> str. Messages are a list of
{"role": "user"|"assistant", "content": str}. The harness logs every call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Protocol


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
      "auto"        - API if ANTHROPIC_API_KEY set, else claude CLI, else error.
      "api"         - force Anthropic API.
      "cli"         - force claude CLI.
      "mock"        - force mock (no responder; returns "OK").
    """
    if prefer == "mock":
        return MockBackend(model=model or "mock")
    if prefer == "api" or (prefer == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicAPIBackend(model=model or "claude-haiku-4-5")
    if prefer == "cli" or prefer == "auto":
        if shutil.which("claude") is not None:
            return ClaudeCLIBackend(model=model or "haiku")
    raise RuntimeError(
        "no LLM backend available: set ANTHROPIC_API_KEY, install the claude CLI, "
        "or use prefer='mock'"
    )
