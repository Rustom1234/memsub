"""SDK and MCP server tests. The MCP loop is also driven over real stdio."""

import json
import subprocess
import sys

import pytest

from memsub.mcp_server import TOOLS, call_tool, handle_message
from memsub.namespaces import PermissionDenied
from memsub.sdk import MemoryClient
from memsub.store import Episode, format_episode


@pytest.fixture
def client(tmp_path):
    c = MemoryClient(db_path=tmp_path / "mem.db", agent="tester", namespace="default")
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# SDK
# --------------------------------------------------------------------------- #
def test_sdk_add_search_roundtrip(client):
    ep = client.add("The deploy port of the amber-router is 8443.")
    assert ep.provenance["written_by"] == "tester"
    hits = client.search("what port does the amber-router use?")
    assert hits and hits[0].episode.id == ep.id
    # search logged retrieval + injection events
    assert client.store.events(event="retrieved")
    assert client.store.events(event="injected")


def test_sdk_inject_respects_budget(client):
    for i in range(30):
        client.add(f"memory number {i} about the amber-router deploy port {1000 + i}")
    text = client.inject("amber-router deploy port", budget_tokens=50)
    assert text
    assert len(text) // 4 <= 50 + 10  # chars/4 approximation, small slack


def test_sdk_forget_and_list(client):
    a = client.add("keep this")
    b = client.add("forget this")
    assert client.forget(b.id, reason="test") is True
    ids = {e.id for e in client.list()}
    assert ids == {a.id}
    assert client.store.status(b.id) == "forgotten"
    assert client.forget("missing-id") is False


def test_sdk_prefix_resolution_and_audit(client):
    ep = client.add("auditable fact")
    client.search("auditable")
    record = client.audit(ep.id[:10])
    assert record is not None
    assert record["counts"]["retrieved"] >= 1


# --------------------------------------------------------------------------- #
# format_episode / with_dates (item 3, 2026-07-18 Wave 1)
# --------------------------------------------------------------------------- #
def _ep(content: str, session_date: str = "") -> Episode:
    prov = {"session_date": session_date} if session_date else {}
    return Episode(content=content, run_id="r", session_id="s", provenance=prov)


def test_format_episode_default_off_is_byte_identical():
    ep = _ep("Joanna: I visited Bali last year.", session_date="11 November 2022")
    assert format_episode(ep) == ep.content
    assert format_episode(ep, with_dates=False) == ep.content


def test_format_episode_with_dates_prefixes_session_date():
    ep = _ep("Joanna: I visited Bali last year.", session_date="11 November 2022")
    assert format_episode(ep, with_dates=True) == "[11 November 2022] Joanna: I visited Bali last year."


def test_format_episode_with_dates_no_session_date_falls_back():
    ep = _ep("no date on this one")
    assert format_episode(ep, with_dates=True) == ep.content


def test_sdk_inject_with_dates_false_is_byte_identical_to_before_flag(client):
    client.add("Joanna: I visited Bali last year.", provenance={"session_date": "11 November 2022"})
    client.add("plain memory, no session date")
    default_text = client.inject("Bali")
    off_text = client.inject("Bali", with_dates=False)
    assert default_text == off_text
    assert "[11 November 2022]" not in default_text


def test_sdk_inject_with_dates_true_prefixes_dated_episodes(client):
    client.add("Joanna: I visited Bali last year.", provenance={"session_date": "11 November 2022"})
    client.add("plain memory, no session date")
    text = client.inject("Bali", with_dates=True)
    assert "[11 November 2022] Joanna: I visited Bali last year." in text


def test_sdk_namespace_acl_enforced(tmp_path):
    owner = MemoryClient(db_path=tmp_path / "m.db", agent="owner", namespace="shared:team")
    owner.namespaces.create("shared:team", owner="owner")
    owner.add("team fact")
    intruder = MemoryClient(db_path=tmp_path / "m.db", agent="intruder", namespace="shared:team")
    with pytest.raises(PermissionDenied):
        intruder.search("team fact")
    with pytest.raises(PermissionDenied):
        intruder.add("vandalism")
    owner.close()
    intruder.close()


# --------------------------------------------------------------------------- #
# MCP: message handling in-process
# --------------------------------------------------------------------------- #
def test_mcp_initialize_and_tools_list(client):
    resp = handle_message(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18"}})
    assert resp["result"]["serverInfo"]["name"] == "memsub"
    resp = handle_message(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"add_memory", "search_memory", "list_memories", "forget_memory"}
    for t in TOOLS:
        assert t["inputSchema"]["type"] == "object"


def test_mcp_notification_gets_no_response(client):
    assert handle_message(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_unknown_method_errors(client):
    resp = handle_message(client, {"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert resp["error"]["code"] == -32601


def test_mcp_tool_flow(client):
    out = call_tool(client, "add_memory", {"content": "Rustom prefers plain markdown.", "kind": "preference"})
    assert "stored memory" in out["content"][0]["text"]
    out = call_tool(client, "search_memory", {"query": "what format does Rustom prefer?"})
    assert "plain markdown" in out["content"][0]["text"]
    mem_id = out["content"][0]["text"].split("]")[0].strip("[")
    out = call_tool(client, "list_memories", {})
    assert "plain markdown" in out["content"][0]["text"]
    out = call_tool(client, "forget_memory", {"memory_id": mem_id, "reason": "test"})
    assert out["content"][0]["text"] == "forgotten"
    out = call_tool(client, "list_memories", {})
    assert "no active memories" in out["content"][0]["text"]


def test_mcp_bad_args_and_unknown_tool(client):
    out = call_tool(client, "add_memory", {})
    assert out.get("isError") is True
    out = call_tool(client, "warp_drive", {})
    assert out.get("isError") is True


# --------------------------------------------------------------------------- #
# MCP: real stdio round-trip
# --------------------------------------------------------------------------- #
def test_mcp_stdio_roundtrip(tmp_path):
    db = tmp_path / "mcp.db"
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "add_memory", "arguments": {"content": "stdio test memory"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "search_memory", "arguments": {"query": "stdio test"}}},
    ]
    payload = "\n".join(json.dumps(m) for m in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "memsub.cli", "mcp", "--db", str(db)],
        input=payload, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    responses = [json.loads(line) for line in proc.stdout.strip().splitlines()]
    assert len(responses) == 3  # notification produced no response
    by_id = {r["id"]: r for r in responses}
    assert by_id[1]["result"]["serverInfo"]["name"] == "memsub"
    assert "stored memory" in by_id[2]["result"]["content"][0]["text"]
    assert "stdio test memory" in by_id[3]["result"]["content"][0]["text"]


# --------------------------------------------------------------------------- #
# Retrieval v2 as product default
# --------------------------------------------------------------------------- #
def test_sdk_hybrid_is_default_and_dense_available(tmp_path):
    c = MemoryClient(db_path=tmp_path / "m.db")
    assert c.retrieval == "hybrid"
    from memsub.hybrid import HybridRetriever

    c.add("The deploy port of the amber-router is 8443.")
    assert isinstance(c._get_index("default"), HybridRetriever)
    c.close()
    d = MemoryClient(db_path=tmp_path / "m.db", retrieval="dense")
    assert d.search("amber-router port?")  # dense path still works
    d.close()
    with pytest.raises(ValueError):
        MemoryClient(db_path=tmp_path / "m.db", retrieval="magic")


def test_sdk_hybrid_search_logs_audit_events(tmp_path):
    c = MemoryClient(db_path=tmp_path / "m.db")
    ep = c.add("Rustom keeps notes in plain markdown.")
    hits = c.search("what format for notes?")
    assert hits and hits[0].episode.id == ep.id
    assert c.store.events(event="retrieved")
    assert c.store.events(event="injected")
    c.close()


def test_cli_search_uses_hybrid(tmp_path, capsys):
    from memsub.cli import main as cli_main

    db = tmp_path / "m.db"
    c = MemoryClient(db_path=db)
    c.add("The staging server lives at 10.0.0.7.")
    c.add("Lunch was a salad bowl.")
    c.close()
    assert cli_main(["search", "where is the staging server?", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "10.0.0.7" in out
    assert cli_main(["search", "x", "--db", str(tmp_path / "missing.db")]) == 1
