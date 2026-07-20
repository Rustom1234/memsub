"""`memsub recall` CLI tests: read-only, no LLM calls anywhere in this path.

See memsub/cli.py:_cmd_recall for how the relevance floor was tuned and why
it is a separate cosine check rather than HybridRetriever.search's own
rank-derived score.
"""

import time

from memsub.cli import main
from memsub import provenance
from memsub.store import Episode, EpisodicStore

NOW = time.time()


def _seed(db_path, key_path):
    """One clearly-on-topic user_stated fact, one distractor, all signed so
    the recall path's channel-tag rendering ("user_stated" vs "legacy") is
    exercised too."""
    key = provenance.load_or_create_key(key_path)
    store = EpisodicStore(db_path)
    on_topic = Episode(
        content="The quantum flux capacitor requires exactly 88 gigawatts to operate.",
        run_id="r1", session_id="s1", kind="fact", ts=NOW - 3600, namespace="claude-code",
    )
    distractor = Episode(
        content="The office coffee machine was replaced last Tuesday.",
        run_id="r1", session_id="s1", kind="fact", ts=NOW - 7200, namespace="claude-code",
    )
    store.append(provenance.tag_episode(on_topic, provenance.CHANNEL_USER_STATED, key=key))
    store.append(provenance.tag_episode(distractor, provenance.CHANNEL_USER_STATED, key=key))
    store.close()
    return key


def test_recall_prints_relevant_hit_with_channel_tag_and_age(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    key_path = tmp_path / "provenance.key"
    monkeypatch.setenv("MEMSUB_PROVENANCE_KEY_PATH", str(key_path))
    _seed(db, key_path)

    rc = main([
        "recall", "--db", str(db), "--namespace", "claude-code",
        "--query", "how many gigawatts does the flux capacitor need",
        "--k", "3", "--max-chars", "700",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "flux capacitor" in out
    assert "[user_stated," in out
    assert "ago]" in out


def test_recall_silent_on_irrelevant_query(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    key_path = tmp_path / "provenance.key"
    monkeypatch.setenv("MEMSUB_PROVENANCE_KEY_PATH", str(key_path))
    _seed(db, key_path)

    rc = main([
        "recall", "--db", str(db), "--namespace", "claude-code",
        "--query", "zzqx unrelated banana spaceship gibberish topic",
        "--k", "3", "--max-chars", "700",
    ])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_recall_missing_store_is_silent_not_an_error(tmp_path, capsys):
    db = tmp_path / "missing.db"
    rc = main([
        "recall", "--db", str(db), "--namespace", "claude-code",
        "--query", "anything at all",
    ])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_recall_never_injects_quarantined_content(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    key_path = tmp_path / "provenance.key"
    monkeypatch.setenv("MEMSUB_PROVENANCE_KEY_PATH", str(key_path))
    key = provenance.load_or_create_key(key_path)
    store = EpisodicStore(db)
    tampered = Episode(
        content="The quantum flux capacitor requires exactly 88 gigawatts to operate.",
        run_id="r1", session_id="s1", kind="fact", ts=NOW - 1000, namespace="claude-code",
    )
    tagged = provenance.tag_episode(tampered, provenance.CHANNEL_USER_STATED, key=key)
    # Corrupt the signature after tagging: channel_status must quarantine this,
    # and _cmd_recall must never print it, regardless of how relevant it is.
    bad_provenance = dict(tagged.provenance)
    bad_provenance["channel_sig"] = "0" * 64
    forged = Episode(
        content=tagged.content, run_id=tagged.run_id, session_id=tagged.session_id,
        kind=tagged.kind, provenance=bad_provenance, namespace=tagged.namespace,
        ts=tagged.ts, id=tagged.id,
    )
    store.append(forged)
    store.close()

    rc = main([
        "recall", "--db", str(db), "--namespace", "claude-code",
        "--query", "how many gigawatts does the flux capacitor need",
        "--k", "3", "--max-chars", "700",
    ])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_recall_unsigned_legacy_tagged_legacy(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    key_path = tmp_path / "provenance.key"
    monkeypatch.setenv("MEMSUB_PROVENANCE_KEY_PATH", str(key_path))
    store = EpisodicStore(db)
    # No provenance.tag_episode call: this is an unsigned-legacy row.
    store.append(Episode(
        content="The quantum flux capacitor requires exactly 88 gigawatts to operate.",
        run_id="r1", session_id="s1", kind="fact", ts=NOW - 1000, namespace="claude-code",
    ))
    store.close()

    rc = main([
        "recall", "--db", str(db), "--namespace", "claude-code",
        "--query", "how many gigawatts does the flux capacitor need",
        "--k", "3", "--max-chars", "700",
    ])
    assert rc == 0
    assert "[legacy," in capsys.readouterr().out


def test_recall_respects_max_chars_budget(tmp_path, monkeypatch, capsys):
    db = tmp_path / "mem.db"
    key_path = tmp_path / "provenance.key"
    monkeypatch.setenv("MEMSUB_PROVENANCE_KEY_PATH", str(key_path))
    _seed(db, key_path)

    rc = main([
        "recall", "--db", str(db), "--namespace", "claude-code",
        "--query", "how many gigawatts does the flux capacitor need",
        "--k", "3", "--max-chars", "5",  # smaller than even one line
    ])
    assert rc == 0
    assert capsys.readouterr().out == ""
