"""Phase 6 end-to-end: MCP server -> discovery -> agent tool calling.

Runs against a live backend. Exercises:
* the mounted MCP server's protocol endpoint (initialize/list/call)
* discovery registering mcp_* proxy tools into the agent registry
* direct test invocation of weather / calculator / http_request (SSRF guard)
* an agent chat whose arithmetic routes through the MCP calculator
* knowledge_search via MCP with a real embedded knowledge base

Usage (backend running on :8000):
    python docs/e2e_phase6.py
"""
from __future__ import annotations

import argparse
import codecs
import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"
MCP_ENDPOINT = "http://127.0.0.1:8000/api/mcp"

parser = argparse.ArgumentParser()
parser.add_argument("--base", default=BASE)
args = parser.parse_args()
BASE = args.base.rstrip("/")


def call(method, path, body=None, token=None, raw=None, filename=None, timeout=120):
    url = f"{BASE}{path}"
    headers = {}
    data = None
    if raw is not None:
        boundary = "----phase6" + uuid.uuid4().hex
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
        ).encode("utf-8")
        data = head + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode()
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, json.loads(payload)
        except ValueError:
            return e.code, payload


def mcp_rpc(method, params=None, request_id=None, session=None, notification=False):
    """One raw JSON-RPC message against the mounted MCP server."""
    body = {"jsonrpc": "2.0"}
    if not notification:
        body["id"] = request_id if request_id is not None else 1
    body["method"] = method
    if params is not None:
        body["params"] = params
    headers = {"Content-Type": "application/json"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(
        MCP_ENDPOINT, data=json.dumps(body).encode(), method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode()
            return resp.status, resp.headers.get("Mcp-Session-Id"), (
                json.loads(payload) if payload else None
            )
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, e.headers.get("Mcp-Session-Id"), json.loads(payload)
        except ValueError:
            return e.code, None, payload


def sse_chat(token, conv_id, content, timeout=180):
    url = f"{BASE}/conversations/{conv_id}/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": content}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    events = []
    decoder = codecs.getincrementaldecoder("utf-8")()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buffer = ""
        while True:
            chunk = resp.read(512)
            if not chunk:
                break
            buffer += decoder.decode(chunk, False)
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                event, data = "", ""
                for line in frame.split("\n"):
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data += line[5:].strip()
                if event:
                    events.append((event, json.loads(data) if data else {}))
    return events


fails: list[str] = []


def check(label, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        fails.append(label)
    print(f"  [{mark}] {label}{(' -> ' + str(extra)) if extra else ''}")


user = "e6" + uuid.uuid4().hex[:8]
password = "e2epassword123"

print("=" * 64)
print("1) register + login")
st, _ = call("POST", "/auth/register",
             {"username": user, "email": f"{user}@example.com", "password": password})
check("register 201", st == 201, st)
st, tok = call("POST", "/auth/login", {"username": user, "password": password})
token = tok["access_token"]
check("login", bool(token))

print("=" * 64)
print("2) MCP protocol endpoint (raw JSON-RPC)")
st, session, result = mcp_rpc(
    "initialize",
    {"protocolVersion": "2025-06-18", "capabilities": {},
     "clientInfo": {"name": "e2e", "version": "1.0"}},
    request_id=1,
)
check("initialize 200", st == 200, st)
check("session id issued", bool(session), session)
check("serverInfo present",
      ((result or {}).get("result", {}).get("serverInfo", {}).get("name")
       == "ai-platform-mcp"),
      (result or {}).get("result", {}).get("serverInfo"))

st, _, body = mcp_rpc("tools/list", request_id=2, session=session)
remote_names = {t["name"] for t in (body or {}).get("result", {}).get("tools", [])}
check("tools/list 200", st == 200, st)
check("five framework tools exposed",
      {"calculator", "knowledge_search", "weather", "http_request", "python_analysis"} <= remote_names,
      remote_names)

st, _, body = mcp_rpc(
    "tools/call", {"name": "calculator", "arguments": {"expression": "21*2"}},
    request_id=3, session=session,
)
text = "".join(
    block.get("text", "") for block in (body or {}).get("result", {}).get("content", [])
)
check("tools/call computes", st == 200 and "42" in text, text[:80])

st_n, _, _ = mcp_rpc("notifications/initialized", session=session, notification=True)
check("initialized notification 202", st_n == 202, st_n)

st, _, body = mcp_rpc("tools/list", request_id=9, session="bogus-session")
check("bogus session rejected (404)", st == 404, st)

print("=" * 64)
print("3) discovery through the management API")
st, payload = call("GET", "/mcp/tools", token=token)
check("mcp tools listed", st == 200 and payload["status"]["state"] == "connected",
      payload.get("status"))
proxy_names = {t["name"] for t in payload.get("tools", [])}
check("proxies registered as mcp_*",
      {"mcp_calculator", "mcp_weather", "mcp_http_request", "mcp_knowledge_search",
       "mcp_python_analysis"} <= proxy_names,
      proxy_names)

st, payload = call("POST", "/mcp/refresh", token=token)
check("refresh re-discovers", st == 200 and payload["status"]["state"] == "connected",
      payload.get("status"))

print("=" * 64)
print("4) direct tool calls via the test panel")
st, res = call("POST", "/mcp/call",
               {"tool": "calculator", "arguments": {"expression": "10/4"}}, token=token)
check("calculator via mcp", st == 200 and "2.5" in res["text"], res.get("text", "")[:80])

st, res = call("POST", "/mcp/call", {"tool": "weather", "arguments": {"city": "杭州"}}, token=token)
check("weather returns data (real or labelled sample)",
      st == 200 and not res["is_error"] and "气温" in res["text"], res.get("text", "")[:80])

st, res = call("POST", "/mcp/call",
               {"tool": "http_request", "arguments": {"url": "http://127.0.0.1:8000/api/v1/health"}},
               token=token)
check("SSRF guard blocks loopback", st == 200 and res["is_error"], res.get("text", "")[:80])

print("=" * 64)
print("5) agent chat routes arithmetic through mcp_calculator")
st, agent = call("POST", "/agents", {"name": "MCP 演示", "tools": ["mcp_calculator"]}, token=token)
check("agent with mcp tool created", st == 201, st)
st, conv = call("POST", "/conversations", {"agent_id": agent["id"]}, token=token)
conv_id = conv["id"]

events = sse_chat(token, conv_id, "计算 (1200+980)*1.12 等于多少")
steps = [d for n, d in events if n == "step"]
answer = "".join(d.get("text", "") for n, d in events if n == "delta")
check("mcp_calculator called",
      any(s.get("tool") == "mcp_calculator" for s in steps),
      [(s.get("type"), s.get("tool")) for s in steps])
check("answer carries the result", "2441.6" in answer, answer[:80])

print("=" * 64)
print("6) knowledge_search through MCP with a real KB")
st, kb = call("POST", "/knowledge-bases", {"name": "MCP 知识库"}, token=token)
kb_id = kb["id"]
policy = ("发票开具后十五个工作日内到账。" * 10).encode("utf-8")
st, up = call("POST", f"/knowledge-bases/{kb_id}/documents", raw=policy,
              filename="发票政策.txt", token=token)
deadline = time.time() + 90
while time.time() < deadline:
    st, task = call("GET", f"/tasks/{up['task']['id']}", token=token)
    if task.get("status") in ("completed", "failed"):
        break
    time.sleep(1.5)
check("document embedded", task.get("status") == "completed", task.get("status"))

st, res = call("POST", "/mcp/call",
               {"tool": "knowledge_search",
                "arguments": {"kb_id": kb_id, "query": "发票多久到账"}}, token=token)
check("mcp knowledge_search finds the policy",
      st == 200 and not res["is_error"] and "发票" in res["text"] and "第 1 页" in res["text"],
      res.get("text", "")[:80])

print("=" * 64)
print("7) cleanup")
st, _ = call("DELETE", f"/conversations/{conv_id}", token=token)
check("delete conv 204", st == 204, st)
st, _ = call("DELETE", f"/agents/{agent['id']}", token=token)
check("delete agent 204", st == 204, st)
st, _ = call("DELETE", f"/knowledge-bases/{kb_id}", token=token)
check("delete kb 204", st == 204, st)

print("=" * 64)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S): {fails}")
    raise SystemExit(1)
print("RESULT: ALL CHECKS PASSED")
