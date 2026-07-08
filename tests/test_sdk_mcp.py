"""SDK and MCP server tests. The MCP loop is also driven over real stdio."""

import json
import subprocess
import sys

import pytest

from memsub.mcp_server import TOOLS, call_tool, handle_message
from memsub.namespaces import PermissionDenied
from memsub.sdk import MemoryClient


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
