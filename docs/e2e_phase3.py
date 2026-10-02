"""Phase 3 end-to-end: upload -> embed -> vector search -> RAG answer -> re-embed.

Runs against a live backend (SQLite preview or Docker). Exercises the inline
fallback path (no Redis), which is what a local preview uses.

Covers the Phase 3 acceptance points from the framework document:
* chunks come out of ingestion already embedded (``embedding_model`` set)
* vector search returns top-K hits with filename / page / score
* RAG Q&A answers with document + page citations (mock LLM is labelled)
* cross-user isolation on the new endpoints
* re-embed endpoint regenerates vectors and records the provider

Usage (backend running on :8000):
    python docs/e2e_phase3.py
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000/api/v1"

parser = argparse.ArgumentParser()
parser.add_argument("--base", default=BASE, help="API root, e.g. http://127.0.0.1:8000/api/v1")
args = parser.parse_args()
BASE = args.base.rstrip("/")


def call(method, path, body=None, token=None):
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read().decode()
            return resp.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, json.loads(payload)
        except ValueError:
            return e.code, payload


fails: list[str] = []


def check(label, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        fails.append(label)
    print(f"  [{mark}] {label}{(' -> ' + str(extra)) if extra else ''}")


def wait_task_completed(task_id, token, timeout=120):
    """Poll a task until it leaves pending/running (real-worker friendly)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, task = call("GET", f"/tasks/{task_id}", token=token)
        if st == 200 and task.get("status") in ("completed", "failed"):
            return task
        time.sleep(1.5)
    return {"status": "timeout"}


def wait_doc_completed(kb_id, doc_id, token, timeout=120):
    """Poll the document list until the document reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, docs = call("GET", f"/knowledge-bases/{kb_id}/documents", token=token)
        if st == 200:
            for d in docs:
                if d["id"] == doc_id and d["status"] in ("completed", "failed"):
                    return d
        time.sleep(1.5)
    return None


user = "e3" + uuid.uuid4().hex[:8]
password = "e2epassword123"

print("=" * 64)
print("1) register + login")
st, _ = call("POST", "/auth/register",
             {"username": user, "email": f"{user}@example.com", "password": password})
check("register 201", st == 201, st)
st, tok = call("POST", "/auth/login", {"username": user, "password": password})
check("login 200", st == 200, st)
token = tok.get("access_token") if isinstance(tok, dict) else None
check("token issued", bool(token))

print("=" * 64)
print("2) knowledge base + upload (ingestion must embed chunks)")
st, kb = call("POST", "/knowledge-bases", {"name": "Phase3 验收库"}, token=token)
kb_id = kb["id"]
check("kb created", st == 201 and kb_id is not None, st)

doc_body = (
    "2024 年度销售报告\n\n"
    "本年度华东区销售额 1200 万，同比增长 12%，三月出现异常峰值。\n\n"
    "产品手册\n\n"
    "pgvector 是 PostgreSQL 的向量检索扩展，支持 HNSW 索引与余弦距离。\n\n"
) + ("补充说明：所有数据均来自内部系统，仅供演示检索与问答流程使用。" * 40)

boundary = "----phase3" + uuid.uuid4().hex
raw = doc_body.encode("utf-8")
head = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="销售报告.txt"\r\n'
    f"Content-Type: text/plain\r\n\r\n"
).encode("utf-8")
data = head + raw + f"\r\n--{boundary}--\r\n".encode("utf-8")
req = urllib.request.Request(
    f"{BASE}/knowledge-bases/{kb_id}/documents",
    data=data,
    method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
             "Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(req, timeout=120) as resp:
    upload = json.loads(resp.read().decode())
doc = upload["document"]
task = upload["task"]
check("upload 201 accepted", doc["status"] in ("completed", "pending", "processing"),
      doc["status"])
# With a real Celery worker the parse+embed runs after the response returns;
# wait for the terminal state before asserting on results.
if doc["status"] != "completed":
    task = wait_task_completed(task["id"], token)
    doc = wait_doc_completed(kb_id, doc["id"], token) or doc
    check("task eventually completed", task.get("status") == "completed", task.get("status"))
check("document completed", doc["status"] == "completed", doc["status"])
check("chunks produced", doc["chunk_count"] > 0, doc["chunk_count"])
check("document embedding_model set", bool(doc.get("embedding_model")), doc.get("embedding_model"))
check("task result carries embedding_model",
      (task.get("result") or {}).get("embedding_model") == doc.get("embedding_model"),
      (task.get("result") or {}).get("embedding_model"))
doc_id = doc["id"]

print("=" * 64)
print("3) vector search: top-K with source locators")
st, search = call("POST", f"/knowledge-bases/{kb_id}/search",
                  {"query": "三月销售额异常", "top_k": 3}, token=token)
check("search 200", st == 200, st)
hits = search.get("hits") or []
check("hits returned", len(hits) > 0, len(hits))
if hits:
    top = hits[0]
    check("top hit is the sales doc", "销售" in top["filename"], top["filename"])
    check("top hit mentions the query topic",
          "销售额" in top["snippet"] or "三月" in top["snippet"], top["snippet"][:60])
    check("top hit carries page number", top["page_number"] is not None, top["page_number"])
    check("vector score in range", 0.0 < top["vector_score"] <= 1.0, top["vector_score"])
    check("ranks descend by final score",
          all(hits[i]["final_score"] >= hits[i + 1]["final_score"] for i in range(len(hits) - 1)),
          [round(h["final_score"], 3) for h in hits])
    check("top_k respected", len(hits) <= 3, len(hits))
    check("reranker reported", search.get("reranker"), search.get("reranker"))
    check("embedding model reported", search.get("embedding_model"), search.get("embedding_model"))

print("=" * 64)
print("4) RAG query: grounded answer + citations")
st, qa = call("POST", f"/knowledge-bases/{kb_id}/query",
              {"query": "三月销售额出现了什么情况？"}, token=token)
check("query 200", st == 200, st)
check("answer non-empty", bool(qa.get("answer")), str(qa.get("answer"))[:80])
check("mock LLM labelled itself", "mock" in qa.get("llm_provider", ""), qa.get("llm_provider"))
check("answer cites sources", "来源" in qa.get("answer", ""), "")
qa_sources = qa.get("sources") or []
check("sources attached", len(qa_sources) > 0, len(qa_sources))
if qa_sources:
    src = qa_sources[0]
    check("source has filename", bool(src["filename"]), src["filename"])
    check("source has page", src["page_number"] is not None, src["page_number"])
    check("source has snippet", bool(src["snippet"]), "")
check("duration reported", isinstance(qa.get("duration_ms"), int), qa.get("duration_ms"))

print("=" * 64)
print("5) blank query rejected")
st, err = call("POST", f"/knowledge-bases/{kb_id}/search", {"query": "   "}, token=token)
check("blank query 422", st == 422, st)

print("=" * 64)
print("6) re-embed endpoint regenerates vectors")
st, reembed = call("POST", f"/knowledge-bases/{kb_id}/documents/{doc_id}/reembed", token=token)
check("reembed 202", st == 202, f"{st} {str(reembed)[:160]}")
if st == 202:
    check("task type document.embed", reembed["task"]["type"] == "document.embed",
          reembed["task"]["type"])
    embed_task = wait_task_completed(reembed["task"]["id"], token)
    check("reembed completed", embed_task["status"] == "completed", embed_task["status"])
    st, docs_now = call("GET", f"/knowledge-bases/{kb_id}/documents", token=token)
    re_doc = next((d for d in docs_now if d["id"] == doc_id), {})
    check("document re-embedded", bool(re_doc.get("embedding_model")),
          re_doc.get("embedding_model"))
    st, search2 = call("POST", f"/knowledge-bases/{kb_id}/search",
                       {"query": "pgvector HNSW 索引"}, token=token)
    check("search still works after re-embed", st == 200 and len(search2.get("hits", [])) > 0, st)

print("=" * 64)
print("7) cross-user isolation on the new endpoints")
user2 = "e3b" + uuid.uuid4().hex[:8]
call("POST", "/auth/register", {"username": user2, "email": f"{user2}@example.com", "password": password})
st, tok2 = call("POST", "/auth/login", {"username": user2, "password": password})
token2 = tok2.get("access_token")
st, _ = call("POST", f"/knowledge-bases/{kb_id}/search", {"query": "anything"}, token=token2)
check("other user search 404", st == 404, st)
st, _ = call("POST", f"/knowledge-bases/{kb_id}/query", {"query": "anything"}, token=token2)
check("other user query 404", st == 404, st)
st, _ = call("POST", f"/knowledge-bases/{kb_id}/documents/{doc_id}/reembed", token=token2)
check("other user reembed 404", st == 404, st)

print("=" * 64)
print("8) unauthenticated access blocked")
st, _ = call("POST", f"/knowledge-bases/{kb_id}/search", {"query": "x"})
check("no token -> 401", st == 401, st)

print("=" * 64)
print("9) RAG on an empty knowledge base answers honestly")
st, kb2 = call("POST", "/knowledge-bases", {"name": "空库"}, token=token)
empty_kb_id = kb2["id"]
st, qa2 = call("POST", f"/knowledge-bases/{empty_kb_id}/query", {"query": "任何问题"}, token=token)
check("empty kb query 200", st == 200, st)
check("no sources for empty kb", qa2.get("sources") == [], qa2.get("sources"))
check("answer reports missing evidence",
      "没有" in qa2.get("answer", "") or "mock" in qa2.get("answer", ""),
      str(qa2.get("answer"))[:80])

print("=" * 64)
print("10) cleanup")
st, _ = call("DELETE", f"/knowledge-bases/{kb_id}", token=token)
check("delete kb 204", st == 204, st)
st, _ = call("DELETE", f"/knowledge-bases/{empty_kb_id}", token=token)
check("delete empty kb 204", st == 204, st)

print("=" * 64)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S): {fails}")
    raise SystemExit(1)
print("RESULT: ALL CHECKS PASSED")
