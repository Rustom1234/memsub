"""Aider parser: git commits authored "(aider)" are git's own snapshots, so
index_commits(...) reads exact content via `git show <hash>:<path>`. This
is the highest-confidence of the three new parsers: no format guessing."""

import subprocess

from memsub.filehist import versions
from memsub.parsers.aider import index_commits
from memsub.store import EpisodicStore


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


def _commit(cwd, author, message, files):
    for fname, content in files.items():
        (cwd / fname).write_text(content)
    _git(cwd, "add", "-A")
    name, email = author
    _git(cwd, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-qm", message)


def test_only_aider_authored_commits_are_indexed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, ("Rustom", "r@t.com"), "human commit", {"a.py": "human\n"})
    _commit(repo, ("Rustom (aider)", "r@t.com"), "aider commit", {"a.py": "aider wrote this\n"})

    store = EpisodicStore(tmp_path / "m.db")
    added = index_commits(store, repo)
    assert added == 1
    vs = versions(store, (repo / "a.py").resolve())
    assert len(vs) == 1
    assert vs[0].kind == "write"


def test_index_commits_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, ("Rustom (aider)", "r@t.com"), "aider commit", {"a.py": "v1\n"})

    store = EpisodicStore(tmp_path / "m.db")
    assert index_commits(store, repo) == 1
    assert index_commits(store, repo) == 0  # already checkpointed


def test_no_aider_history_is_a_silent_noop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, ("Rustom", "r@t.com"), "human only", {"a.py": "v1\n"})

    store = EpisodicStore(tmp_path / "m.db")
    assert index_commits(store, repo) == 0


def test_non_repo_dir_is_a_silent_noop(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    store = EpisodicStore(tmp_path / "m.db")
    assert index_commits(store, plain) == 0
