"""MCP server for memsub: stdio transport, stdlib only, zero dependencies.

Speaks MCP over newline-delimited JSON-RPC 2.0 on stdin/stdout, which is what
`claude mcp add ... -- memsub mcp` spawns. Nothing runs when the client is not
running; state is one SQLite file.

Tools: add_memory, search_memory, list_memories, forget_memory. Every tool
accepts an optional `namespace`; defaults come from MEMSUB_NAMESPACE and the
agent identity from MEMSUB_AGENT (used for namespace ACL checks and write
provenance).

Wire into Claude Code (see README):

    claude mcp add memsub -- <path-to-venv>/bin/memsub mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .namespaces import PermissionDenied
from .sdk import MemoryClient

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "memsub", "version": "0.1.0"}

_NS_PROP = {
    "namespace": {
        "type": "string",
        "description": "Namespace to operate in (default: the MEMSUB_NAMESPACE env or 'default').",
    }
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_memory",
        "description": (
            "Store one durable memory (a stable fact, preference, or decision worth "
            "remembering across sessions). Write one self-contained sentence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The memory, one self-contained sentence."},
                "kind": {"type": "string", "enum": ["fact", "preference", "decision", "event"], "description": "Category (default fact)."},
                "importance": {"type": "number", "description": "0-1 retrieval weight (default 1.0)."},
                **_NS_PROP,
            },
            "required": ["content"],
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Semantic search over stored memories. Returns the best matches that fit "
            "the token budget, with ids for auditing or forgetting."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you want to remember."},
                "k": {"type": "integer", "description": "Max results (default 6)."},
                "budget_tokens": {"type": "integer", "description": "Hard token cap on returned text (default 700)."},
                **_NS_PROP,
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_memories",
        "description": "List active memories in a namespace, newest last.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max memories to return (default 20)."},
                **_NS_PROP,
            },
        },
    },
    {
        "name": "forget_memory",
        "description": (
            "Forget a memory by id (or unambiguous id prefix). The content is kept in "
            "the audit trail but never retrieved again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory id or unique prefix."},
                "reason": {"type": "string", "description": "Why (recorded in the audit trail)."},
            },
            "required": ["memory_id"],
        },
    },
]


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


def call_tool(client: MemoryClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call. Returns an MCP tool result."""
    try:
        if name == "add_memory":
            ep = client.add(
                content=str(args["content"]),
                kind=str(args.get("kind", "fact")),
                importance=float(args.get("importance", 1.0)),
                namespace=args.get("namespace"),
                provenance={"origin": "mcp_add"},
            )
            return _text_result(f"stored memory {ep.id} in namespace {ep.namespace}")
        if name == "search_memory":
            hits = client.search(
                query=str(args["query"]),
                k=int(args.get("k", 6)),
                budget_tokens=int(args.get("budget_tokens", 700)),
                namespace=args.get("namespace"),
            )
            if not hits:
                return _text_result("no matching memories")
            lines = [f"[{h.episode.id[:12]}] ({h.episode.kind}) {h.episode.content}" for h in hits]
            return _text_result("\n".join(lines))
        if name == "list_memories":
            eps = client.list(namespace=args.get("namespace"), limit=int(args.get("limit", 20)))
            if not eps:
                return _text_result("no active memories in this namespace")
            lines = [f"[{ep.id[:12]}] ({ep.kind}) {ep.content}" for ep in eps]
            return _text_result("\n".join(lines))
        if name == "forget_memory":
            ok = client.forget(str(args["memory_id"]), reason=str(args.get("reason", "mcp_request")))
            return _text_result("forgotten" if ok else "no memory with that id", is_error=not ok)
        return _text_result(f"unknown tool: {name}", is_error=True)
    except PermissionDenied as exc:
        return _text_result(f"permission denied: {exc}", is_error=True)
    except (KeyError, TypeError, ValueError) as exc:
        return _text_result(f"bad arguments: {exc}", is_error=True)


def handle_message(client: MemoryClient, msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns the response, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id")
    if method is None or msg_id is None:
        return None  # notification (e.g. notifications/initialized): no response
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params", {})
        result = call_tool(client, params.get("name", ""), params.get("arguments", {}) or {})
    elif method == "ping":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(client: MemoryClient | None = None) -> int:
    """Blocking stdio loop: one JSON-RPC message per line."""
    client = client or MemoryClient()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(client, msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
