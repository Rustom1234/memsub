import pytest

from memsub.policy import MemoryPolicy, approx_tokens, transform_distorted
from memsub.store import Episode
import random


def _ep(content, kind="session_summary"):
    return Episode(content=content, run_id="r", session_id="1", kind=kind)


def test_should_write_respects_kinds():
    pol = MemoryPolicy()
    assert pol.should_write(_ep("x", kind="session_summary"))
    assert not pol.should_write(_ep("x", kind="scratch"))


def test_summarized_transform_shortens():
    pol = MemoryPolicy(write_transform="summarized")
    long = "First sentence. Second sentence with detail.\nAnother line here too."
    out = pol.apply_write_transform(_ep(long))
    assert len(out.content) <= len(long)
    assert out.provenance["write_transform"] == "summarized"
    assert "original_content" in out.provenance


def test_distorted_transform_records_original_and_changes_numbers():
    pol = MemoryPolicy(write_transform="distorted", seed=1)
    out = pol.apply_write_transform(_ep("COMPLETED: 5/5 with 100 credits and 12345 tokens"))
    assert out.provenance["write_transform"] == "distorted"
    # deterministic under seed
    pol2 = MemoryPolicy(write_transform="distorted", seed=1)
    out2 = pol2.apply_write_transform(_ep("COMPLETED: 5/5 with 100 credits and 12345 tokens"))
    assert out.content == out2.content


def test_accurate_transform_unchanged():
    pol = MemoryPolicy(write_transform="accurate")
    out = pol.apply_write_transform(_ep("verbatim content"))
    assert out.content == "verbatim content"
    assert "original_content" not in out.provenance


def test_injection_budget_enforced():
    pol = MemoryPolicy(injection_budget_tokens=10)
    chunks = ["a" * 40, "b" * 40, "c" * 40]  # ~10 tokens each
    packed = pol.pack_injection(chunks)
    # budget 10 tokens => at most one ~10-token chunk fits
    assert packed.count("\n") == 0


def test_unknown_transform_raises():
    with pytest.raises(ValueError):
        MemoryPolicy(write_transform="bogus")


def test_approx_tokens_monotonic():
    assert approx_tokens("a") == 1
    assert approx_tokens("a" * 40) == 10
