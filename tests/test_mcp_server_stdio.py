"""
The server must run standalone and answer a real MCP client over stdio.

This is the acceptance check for "server runs standalone and can be exercised from the MCP inspector": it starts
the process, performs the handshake, lists the tools and calls one.
"""
import json
import subprocess
import sys
import time

import pytest

from people_ai.config import DB_PATH, REPO_ROOT

pytest.importorskip("mcp")
TIMEOUT = 60


def send(process, message):
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def read_reply(process, request_id, deadline):
    """Read newline-delimited JSON-RPC until the reply to `request_id` arrives."""
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            raise AssertionError(f"server exited: {process.stderr.read()[-2000:]}")
        message = json.loads(line)
        if message.get("id") == request_id:
            return message
    raise AssertionError(f"no reply to request {request_id} within {TIMEOUT}s")


@pytest.fixture(scope="module")
def server():
    if not DB_PATH.exists():
        pytest.skip("data/people.duckdb not found; run `python -m people_ai.generate_data` first")
    process = subprocess.Popen([sys.executable, "-m", "people_ai.mcp_server.server"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1, cwd=REPO_ROOT)
    deadline = time.time() + TIMEOUT
    send(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "pytest", "version": "1"}}})
    reply = read_reply(process, 1, deadline)
    assert reply["result"]["serverInfo"]["name"] == "people-ai"
    send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    yield process
    process.terminate()
    process.wait(timeout=10)


def test_server_lists_its_tools(server):
    send(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = read_reply(server, 2, time.time() + TIMEOUT)["result"]["tools"]
    assert {tool["name"] for tool in tools} == {"list_metrics", "get_definition", "get_metric", "describe_leader",
                                                "search_people", "run_readonly_sql"}


def test_server_answers_a_tool_call(server):
    send(server, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "get_definition", "arguments": {"term": "attrition"}}})
    reply = read_reply(server, 3, time.time() + TIMEOUT)
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert payload["kind"] == "metric" and payload["name"] == "attrition"


def test_server_refuses_an_out_of_scope_request(server):
    """A refusal reaches the client as a tool error, not as an empty table."""
    user_id = 618        # the planted bad manager: terminated, so no grants at all
    send(server, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "get_metric", "arguments": {"name": "headcount", "user_id": user_id}}})
    reply = read_reply(server, 4, time.time() + TIMEOUT)
    payload = json.loads(reply["result"]["content"][0]["text"])
    assert payload["refused"] is True and payload["kind"] == "authorization"
    assert "no access rights" in payload["reason"]
