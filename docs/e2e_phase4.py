"""Phase 4 end-to-end: conversations -> SSE chat -> history -> citations.

Runs against a live backend (Docker stack or local). Exercises:
* conversation CRUD + cross-user isolation
* plain chat over SSE (meta/delta/done, no sources), auto-title, history
* RAG-grounded chat over SSE (sources event, answer quotes the documents)
* dashboard wiring for conversation_count / ai_call_count

Usage (backend running on :8000):
    python docs/e2e_phase4.py
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


def call(method, path, body=None, token=None, timeout=120):
    url = f"{BASE}{path}"
    headers = {}
    data = None
    if body is not None:
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


def sse_chat(token, conv_id, content, timeout=120):
    """POST one chat message and collect the SSE stream."""
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
        assert resp.headers.get_content_type() == "text/event-stream", resp.headers
        buffer = ""
        while True:
            chunk = resp.read(512)
            if not chunk:
                break
            # Chunks can split multi-byte characters; decode incrementally.
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


def wait_task_completed(task_id, token, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, task = call("GET", f"/tasks/{task_id}", token=token)
        if st == 200 and task.get("status") in ("completed", "failed"):
            return task
        time.sleep(1.5)
    return {"status": "timeout"}


user = "e4" + uuid.uuid4().hex[:8]
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
print("2) knowledge base with one embedded document (for RAG chat)")
st, kb = call("POST", "/knowledge-bases", {"name": "Phase4 问答库"}, token=token)
kb_id = kb["id"]
doc_body = ("平台退款政策\n\n七天内支持无理由全额退款，超过七天按使用时长折算。" * 10)
boundary = "----phase4" + uuid.uuid4().hex
raw = doc_body.encode("utf-8")
head = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="退款政策.txt"\r\n'
    f"Content-Type: text/plain\r\n\r\n"
).encode("utf-8")
data = head + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
req = urllib.request.Request(
    f"{BASE}/knowledge-bases/{kb_id}/documents",
    data=data, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
             "Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(req, timeout=120) as resp:
    upload = json.loads(resp.read().decode())
task = wait_task_completed(upload["task"]["id"], token)
check("document embedded", task.get("status") == "completed", task.get("status"))

print("=" * 64)
print("3) conversation CRUD + isolation")
st, conv = call("POST", "/conversations", {"title": "Phase4 会话"}, token=token)
check("create 201", st == 201, st)
conv_id = conv["id"]
st, _ = call("PATCH", f"/conversations/{conv_id}", {"title": "改名会话"}, token=token)
check("rename 200", st == 200, st)

user2 = "e4b" + uuid.uuid4().hex[:8]
call("POST", "/auth/register", {"username": user2, "email": f"{user2}@example.com", "password": password})
st, tok2 = call("POST", "/auth/login", {"username": user2, "password": password})
token2 = tok2["access_token"]
st, _ = call("GET", f"/conversations/{conv_id}", token=token2)
check("stranger detail 404", st == 404, st)

print("=" * 64)
print("4) plain chat over SSE (no KB)")
st, plain = call("POST", "/conversations", {}, token=token)
plain_id = plain["id"]
events = sse_chat(token, plain_id, "用一句话介绍你自己")
names = [n for n, _ in events]
check("stream starts with meta", names[0] == "meta", names[:2])
check("stream ends with done", names[-1] == "done", names[-1])
check("no sources in plain chat", "sources" not in names, names)
answer = "".join(d.get("text", "") for n, d in events if n == "delta")
check("deltas non-empty", bool(answer.strip()), answer[:60])
check("mock labelled", "mock" in answer.lower(), answer[:60])

st, detail = call("GET", f"/conversations/{plain_id}", token=token)
roles = [m["role"] for m in detail["messages"]]
check("history persisted user+assistant", roles == ["user", "assistant"], roles)
check("assistant carries model signature", detail["messages"][-1]["model"], detail["messages"][-1]["model"])
check("auto-title applied", detail["title"] == "用一句话介绍你自己", detail["title"])

# Second message: history must now contain 4 messages.
sse_chat(token, plain_id, "再补充一句")
st, detail = call("GET", f"/conversations/{plain_id}", token=token)
check("history grows to 4", len(detail["messages"]) == 4, len(detail["messages"]))

print("=" * 64)
print("5) RAG-grounded chat over SSE")
st, rag_conv = call("POST", "/conversations", {"knowledge_base_id": kb_id}, token=token)
rag_id = rag_conv["id"]
check("created with kb binding", rag_conv["knowledge_base_id"] == kb_id, rag_conv["knowledge_base_id"])

events = sse_chat(token, rag_id, "退款政策是什么")
names = [n for n, _ in events]
check("sources event emitted", "sources" in names, names)
sources = next((d["sources"] for n, d in events if n == "sources"), [])
check("source points at the document", bool(sources) and sources[0]["filename"] == "退款政策.txt",
      [s.get("filename") for s in sources])
rag_answer = "".join(d.get("text", "") for n, d in events if n == "delta")
check("answer quotes document", "退款" in rag_answer, rag_answer[:80])

st, detail = call("GET", f"/conversations/{rag_id}", token=token)
check("assistant message stores citations",
      detail["messages"][-1]["sources"] and detail["messages"][-1]["sources"][0]["filename"] == "退款政策.txt",
      "stored")

print("=" * 64)
print("6) blank message rejected + cleanup")
st, _ = call("POST", f"/conversations/{plain_id}/chat", {"content": "   "}, token=token)
check("blank content 422", st == 422, st)

st, dash = call("GET", "/dashboard", token=token)
check("dashboard conversation_count = 3", dash["stats"]["conversation_count"] == 3,
      dash["stats"]["conversation_count"])
check("dashboard ai_call_count = 3", dash["stats"]["ai_call_count"] == 3,
      dash["stats"]["ai_call_count"])
trend_sum = sum(p["value"] for p in dash["ai_call_trend"]["points"])
check("ai call trend sums to 3", trend_sum == 3, trend_sum)

for cid in (plain_id, rag_id, conv_id):
    st, _ = call("DELETE", f"/conversations/{cid}", token=token)
    check(f"delete conv {cid} 204", st == 204, st)
st, _ = call("DELETE", f"/knowledge-bases/{kb_id}", token=token)
check("delete kb 204", st == 204, st)

print("=" * 64)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S): {fails}")
    raise SystemExit(1)
print("RESULT: ALL CHECKS PASSED")
