"""Phase 5 end-to-end: agents -> tool calling -> data analysis -> charts.

Runs against a live backend. Exercises:
* tool registry listing
* agent CRUD + cross-user isolation
* Excel/CSV data-file upload with schema snapshot
* agent chat over SSE with the full tool loop:
  - data_analysis (Pandas stats + Matplotlib chart attachment)
  - calculator
  - knowledge_search with citation sources
* chart serving + traversal protection

Usage (backend running on :8000):
    python docs/e2e_phase5.py
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

parser = argparse.ArgumentParser()
parser.add_argument("--base", default=BASE)
args = parser.parse_args()
BASE = args.base.rstrip("/")


def call(method, path, body=None, token=None, raw=None, filename=None, timeout=120):
    url = f"{BASE}{path}"
    headers = {}
    data = None
    if raw is not None:
        boundary = "----phase5" + uuid.uuid4().hex
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
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
            payload = resp.read()
            ctype = resp.headers.get_content_type()
            if ctype == "image/png":
                return resp.status, ("PNG", len(payload))
            text = payload.decode()
            return resp.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, json.loads(payload)
        except ValueError:
            return e.code, payload


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


user = "e5" + uuid.uuid4().hex[:8]
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
print("2) tool registry")
st, tools = call("GET", "/agents/tools", token=token)
names = {t["name"] for t in tools} if isinstance(tools, list) else set()
check("three builtin tools", {"knowledge_search", "calculator", "data_analysis"} <= names, names)

print("=" * 64)
print("3) agent CRUD + isolation")
st, agent = call("POST", "/agents", {"name": "全能助手", "system_prompt": ""}, token=token)
check("agent created", st == 201, st)
agent_id = agent["id"]
user2 = "e5b" + uuid.uuid4().hex[:8]
call("POST", "/auth/register", {"username": user2, "email": f"{user2}@example.com", "password": password})
st, tok2 = call("POST", "/auth/login", {"username": user2, "password": password})
token2 = tok2["access_token"]
st, _ = call("GET", f"/agents/{agent_id}", token=token2)
check("stranger agent 404", st == 404, st)

print("=" * 64)
print("4) data file upload with schema snapshot")
sales_csv = (
    "月份,销售额\n"
    "2024-01,100\n2024-02,120\n2024-03,110\n"
    "2024-04,130\n2024-05,900\n2024-06,105\n"
)
st, data_file = call("POST", "/agents/data-files", raw=sales_csv.encode("utf-8"),
                     filename="sales.csv", token=token)
check("csv uploaded", st == 201, st)
check("row_count = 6", data_file.get("row_count") == 6, data_file.get("row_count"))
check("columns snapshot", data_file.get("column_names") == ["月份", "销售额"],
      data_file.get("column_names"))
st, bad = call("POST", "/agents/data-files", raw=b"MZ", filename="evil.exe", token=token)
check("exe rejected 400", st == 400, st)

print("=" * 64)
print("5) agent chat: data_analysis with chart attachment")
st, conv = call("POST", "/conversations", {"agent_id": agent_id, "title": "数据分析"},
                token=token)
conv_id = conv["id"]
check("conversation bound to agent", conv.get("agent_id") == agent_id, conv.get("agent_id"))

events = sse_chat(token, conv_id, "分析 sales.csv 的各月销售趋势并找出异常月份")
kinds = [n for n, _ in events]
steps = [d for n, d in events if n == "step"]
check("stream ends with done", kinds[-1] == "done", kinds[-3:])
check("data_analysis tool called",
      any(s.get("type") == "tool_call" and s.get("tool") == "data_analysis" for s in steps),
      [(s.get("type"), s.get("tool")) for s in steps])
done = events[-1][1]
attachments = done.get("attachments") or []
check("chart attachment present",
      bool(attachments) and attachments[0]["url"].startswith("/api/v1/agents/charts/"),
      attachments)
answer = "".join(d.get("text", "") for n, d in events if n == "delta")
check("answer contains monthly stats", "按月汇总" in answer, answer[:80])
check("answer flags the outlier", "异常" in answer, "")

chart_url = attachments[0]["url"] if attachments else ""
st, chart = call("GET", chart_url.replace("/api/v1", ""), token=token)
check("chart served as PNG", st == 200 and chart[0] == "PNG", (st, chart if chart else ""))

print("=" * 64)
print("6) agent chat: calculator")
events = sse_chat(token, conv_id, "帮我计算 (1200+980)*1.12 是多少")
steps = [d for n, d in events if n == "step"]
answer = "".join(d.get("text", "") for n, d in events if n == "delta")
# Since Phase 6, discovery may register the MCP-hosted calculator and the
# mock prefers it — both names prove the tool loop works.
check("calculator tool called",
      any(s.get("tool") in ("calculator", "mcp_calculator") for s in steps),
      [(s.get("type"), s.get("tool")) for s in steps])
check("answer carries the result", "2441.6" in answer, answer[:80])

print("=" * 64)
print("7) agent chat: knowledge_search with citations")
st, kb = call("POST", "/knowledge-bases", {"name": "Phase5 知识库"}, token=token)
kb_id = kb["id"]
policy = ("退款政策：七天内无理由全额退款，超过七天按使用时长折算。" * 10).encode("utf-8")
st, up = call("POST", f"/knowledge-bases/{kb_id}/documents", raw=policy,
              filename="退款政策.txt", token=token)
# wait for ingestion
deadline = time.time() + 90
while time.time() < deadline:
    st, task = call("GET", f"/tasks/{up['task']['id']}", token=token)
    if task.get("status") in ("completed", "failed"):
        break
    time.sleep(1.5)
check("policy document embedded", task.get("status") == "completed", task.get("status"))

st, kb_conv = call("POST", "/conversations",
                   {"agent_id": agent_id, "knowledge_base_id": kb_id}, token=token)
events = sse_chat(token, kb_conv["id"], "退款政策是什么")
steps = [d for n, d in events if n == "step"]
done = events[-1][1]
check("knowledge_search called",
      any(s.get("tool") == "knowledge_search" for s in steps),
      [(s.get("type"), s.get("tool")) for s in steps])
check("sources in done event", bool(done.get("sources")), done.get("sources"))
answer = "".join(d.get("text", "") for n, d in events if n == "delta")
check("answer cites the policy", "退款" in answer, answer[:80])

print("=" * 64)
print("8) step log persisted on the assistant message")
st, detail = call("GET", f"/conversations/{conv_id}", token=token)
agent_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
check("assistant messages carry step log",
      all((m.get("tool_calls") or {}).get("steps") for m in agent_msgs),
      len(agent_msgs))

print("=" * 64)
print("9) cleanup")
for cid in (conv_id, kb_conv["id"]):
    st, _ = call("DELETE", f"/conversations/{cid}", token=token)
    check(f"delete conv {cid} 204", st == 204, st)
st, _ = call("DELETE", f"/agents/data-files/{data_file['id']}", token=token)
check("delete data file 204", st == 204, st)
st, _ = call("DELETE", f"/agents/{agent_id}", token=token)
check("delete agent 204", st == 204, st)
st, _ = call("DELETE", f"/knowledge-bases/{kb_id}", token=token)
check("delete kb 204", st == 204, st)

print("=" * 64)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S): {fails}")
    raise SystemExit(1)
print("RESULT: ALL CHECKS PASSED")
