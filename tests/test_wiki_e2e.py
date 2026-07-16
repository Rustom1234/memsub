"""End-to-end wiki-mode journey (mock backend, deterministic embedder):

  ingest -> re-ingest unchanged (no-op) -> edit a page -> re-ingest (supersede)
  -> consolidate --stage (two cross-page contradiction proposals appear)
  -> set verdicts (accept one, reject the other) -> vault apply (accepted
  supersede executes, rejected proposal drops without touching the store).

This is the "LLM wiki that doesn't rot" loop in one test. scripts/wiki_demo.sh
runs the same flow against /tmp with a live backend.
"""

import json

from memsub.cli import main
from memsub.store import EpisodicStore
from memsub.vault import STAGING_DIRNAME

PAGES = {
    "db-arch.md": "# Architecture\n\nThe production database is PostgreSQL.",
    "db-ops.md": "# Operations\n\nThe production database is SQLite.",
    "auth-a.md": "# Auth\n\nThe API authentication uses OAuth2.",
    "auth-b.md": "# Auth notes\n\nThe API authentication uses API keys.",
    "deploy.md": "# Deploy\n\nDeploys run on Fridays.",
}


def _responder(system, messages):
    user = messages[-1]["content"]
    if "contradict" in system.lower():
        # flag every pair the judge was asked about
        n = user.count("PAIR ")
        return json.dumps([{"pair": i, "contradicts": True} for i in range(n)])
    claims = []
    for block in user.split("\n\n"):
        text = block.split("]:", 1)[-1].strip() if "]:" in block else block.strip()
        if not text or text.startswith("#"):
            continue
        claims.append({"content": text, "kind": "fact", "confidence": 0.9})
    return json.dumps(claims)


def _install_mock(monkeypatch):
    from memsub.backends import MockBackend

    monkeypatch.setattr(
        "memsub.cli.build_backend", lambda **kw: MockBackend(responder=_responder)
    )


def _write_pages(pages_dir, mapping):
    pages_dir.mkdir(parents=True, exist_ok=True)
    for name, text in mapping.items():
        (pages_dir / name).write_text(text)


def _staging_notes(vault):
    return sorted((vault / STAGING_DIRNAME).rglob("*.md"))


def test_wiki_journey(tmp_path, monkeypatch, capsys):
    _install_mock(monkeypatch)
    pages = tmp_path / "wiki"
    db = tmp_path / "mem.db"
    vault = tmp_path / "vault"
    _write_pages(pages, PAGES)

    # 1. ingest: five claims land, all active
    assert main(["wiki", "ingest", "--db", str(db), "--pages", str(pages), "--backend", "mock"]) == 0
    capsys.readouterr()
    store = EpisodicStore(db)
    assert len(store.active()) == 5
    store.close()

    # 2. re-ingest unchanged: no-op
    assert main(["wiki", "ingest", "--db", str(db), "--pages", str(pages), "--backend", "mock"]) == 0
    out = capsys.readouterr().out
    assert "5 unchanged" in out
    store = EpisodicStore(db)
    assert len(store.active()) == 5
    assert store.count() == 5  # nothing appended
    store.close()

    # 3. edit a page and re-ingest: its prior claim is superseded, new one appended
    (pages / "deploy.md").write_text("# Deploy\n\nDeploys run on Mondays.")
    assert main(["wiki", "ingest", "--db", str(db), "--pages", str(pages), "--backend", "mock"]) == 0
    out = capsys.readouterr().out
    assert "1 prior claim(s) superseded" in out
    store = EpisodicStore(db)
    active = store.active()
    assert len(active) == 5  # still five active (old deploy retired, new added)
    assert any(e.content == "Deploys run on Mondays." for e in active)
    assert not any(e.content == "Deploys run on Fridays." for e in active)
    assert store.count() == 6  # append-only: the retired claim row remains
    store.close()

    # 4. consolidate --stage: two cross-page contradictions surface as proposals
    assert main([
        "consolidate", "--db", str(db), "--backend", "mock",
        "--stage", "--vault", str(vault),
    ]) == 0
    notes = _staging_notes(vault)
    assert len(notes) == 2
    for n in notes:
        assert 'reason: "contradiction"' in n.read_text()

    # store untouched by staging
    store = EpisodicStore(db)
    assert len(store.active()) == 5
    # identify the two superseded targets each proposal points at
    targets = {}
    for n in notes:
        text = n.read_text()
        ep_id = [l.split('"')[1] for l in text.splitlines() if l.startswith("id:")][0]
        targets[n.name] = ep_id
    store.close()

    # 5. set verdicts: accept the first proposal, reject the second
    accept_note, reject_note = notes[0], notes[1]
    accept_note.write_text(accept_note.read_text().replace('verdict: ""', 'verdict: "accept"'))
    reject_note.write_text(reject_note.read_text().replace('verdict: ""', 'verdict: "reject"'))

    # 6. apply: accepted supersede executes, rejected proposal drops
    assert main(["vault", "--db", str(db), "apply", "--vault", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "1 staged applied" in out
    assert "1 staged rejected" in out

    store = EpisodicStore(db)
    accepted_target = targets[accept_note.name]
    rejected_target = targets[reject_note.name]
    assert store.status(accepted_target) == "superseded"  # accepted action executed
    assert store.status(rejected_target) == "active"       # rejected: untouched
    assert len(store.active()) == 4  # one claim retired by the accepted proposal
    store.close()

    # notes reflect their resolution; re-apply is inert
    assert 'status: "applied"' in accept_note.read_text()
    assert 'status: "rejected"' in reject_note.read_text()
    assert main(["vault", "--db", str(db), "apply", "--vault", str(vault)]) == 0
    out = capsys.readouterr().out
    assert "0 staged applied" in out and "0 staged rejected" in out
