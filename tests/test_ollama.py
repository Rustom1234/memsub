"""Ollama backend and embedder tests. HTTP is monkeypatched, zero network cost."""

import json

import numpy as np
import pytest

from memsub import backends, embed


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, module, payload=None, raises=False):
    def _open(req, timeout=None):
        if raises:
            raise module.urllib.error.URLError("refused")
        return _FakeResponse(payload)

    monkeypatch.setattr(module.urllib.request, "urlopen", _open)


# --------------------------------------------------------------------------- #
# think-block stripping
# --------------------------------------------------------------------------- #
def test_strip_think_blocks_removes_reasoning():
    raw = "<think>\nlet me think about this\n</think>\n\n{\"a\": 1}"
    assert backends.strip_think_blocks(raw) == '{"a": 1}'


def test_strip_think_blocks_no_block_passthrough():
    assert backends.strip_think_blocks("plain text") == "plain text"


def test_strip_think_blocks_unterminated_drops_to_end():
    raw = "<think>\nstill reasoning, never closed"
    assert backends.strip_think_blocks(raw) == ""


def test_strip_think_blocks_multiple_blocks():
    raw = "<think>a</think>keep this<think>b</think>"
    assert backends.strip_think_blocks(raw) == "keep this"


# --------------------------------------------------------------------------- #
# ollama_available
# --------------------------------------------------------------------------- #
def test_ollama_available_true(monkeypatch):
    _patch_urlopen(monkeypatch, backends, payload={"models": []})
    assert backends.ollama_available() is True


def test_ollama_available_false_on_connection_error(monkeypatch):
    _patch_urlopen(monkeypatch, backends, raises=True)
    assert backends.ollama_available() is False


# --------------------------------------------------------------------------- #
# OllamaBackend
# --------------------------------------------------------------------------- #
def test_ollama_backend_strips_think_block(monkeypatch):
    payload = {"message": {"role": "assistant", "content": "<think>reasoning</think>\n\n[]"}}
    _patch_urlopen(monkeypatch, backends, payload=payload)
    b = backends.OllamaBackend(model="qwen3:8b")
    out = b.complete("system", [{"role": "user", "content": "hi"}])
    assert out == "[]"
    assert b.name == "ollama"
    assert b.model == "qwen3:8b"


def test_ollama_backend_raises_on_unreachable(monkeypatch):
    _patch_urlopen(monkeypatch, backends, raises=True)
    b = backends.OllamaBackend()
    with pytest.raises(RuntimeError):
        b.complete("system", [{"role": "user", "content": "hi"}])


def test_build_backend_auto_prefers_ollama_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(backends, "ollama_available", lambda *a, **k: True)
    b = backends.build_backend(prefer="auto")
    assert isinstance(b, backends.OllamaBackend)


def test_build_backend_auto_falls_back_to_cli_without_ollama(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(backends, "ollama_available", lambda *a, **k: False)
    monkeypatch.setattr(backends.shutil, "which", lambda name: "/usr/bin/claude")
    b = backends.build_backend(prefer="auto")
    assert isinstance(b, backends.ClaudeCLIBackend)


def test_build_backend_ollama_forced_raises_if_unreachable(monkeypatch):
    monkeypatch.setattr(backends, "ollama_available", lambda *a, **k: False)
    with pytest.raises(RuntimeError):
        backends.build_backend(prefer="ollama")


# --------------------------------------------------------------------------- #
# OllamaEmbedder / build_embedder
# --------------------------------------------------------------------------- #
def test_ollama_embedder_normalizes_vector(monkeypatch):
    payload = {"embedding": [3.0, 4.0]}
    _patch_urlopen(monkeypatch, embed, payload=payload)
    e = embed.OllamaEmbedder(dim=2)
    vec = e.embed("hello")
    assert vec.shape == (2,)
    assert np.isclose(np.linalg.norm(vec), 1.0)


def test_build_embedder_hashing_default():
    assert isinstance(embed.build_embedder(prefer="hashing"), embed.HashingEmbedder)


def test_build_embedder_auto_uses_ollama_when_available(monkeypatch):
    monkeypatch.setattr(embed, "ollama_available", lambda *a, **k: True)
    e = embed.build_embedder(prefer="auto")
    assert isinstance(e, embed.OllamaEmbedder)


def test_build_embedder_auto_falls_back_to_hashing(monkeypatch):
    monkeypatch.setattr(embed, "ollama_available", lambda *a, **k: False)
    e = embed.build_embedder(prefer="auto")
    assert isinstance(e, embed.HashingEmbedder)


def test_build_embedder_ollama_forced_raises_if_unreachable(monkeypatch):
    monkeypatch.setattr(embed, "ollama_available", lambda *a, **k: False)
    with pytest.raises(RuntimeError):
        embed.build_embedder(prefer="ollama")
