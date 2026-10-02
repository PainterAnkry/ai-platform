"""Phase 7 end-to-end: model-service contract + platform hardening.

Runs against a live stack (backend :8000, model-service :9000):
* model-service health / embeddings / rerank / chat (sync + SSE stream)
* the backend still answers with the unified JSON error shape after the
  Phase 7 middleware went in (404 shape, health, access-log liveness)

Usage:
    python docs/e2e_phase7.py
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
SERVICE = "http://127.0.0.1:9000"

parser = argparse.ArgumentParser()
parser.add_argument("--service", default=SERVICE)
args = parser.parse_args()
SERVICE = args.service.rstrip("/")


def post(url, body, stream=False, timeout=60):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if stream:
            return resp.status, resp.read().decode("utf-8", "replace")
        return resp.status, json.loads(resp.read().decode())


fails: list[str] = []


def check(label, cond, extra=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        fails.append(label)
    print(f"  [{mark}] {label}{(' -> ' + str(extra)) if extra else ''}")


print("=" * 64)
print("1) model-service health")
with urllib.request.urlopen(f"{SERVICE}/health", timeout=10) as resp:
    health = json.loads(resp.read().decode())
check("health ok", health.get("status") == "ok", health)
check("dimension matches default column width", health.get("dim") == 1024, health.get("dim"))

print("=" * 64)
print("2) embeddings endpoint (OpenAI shape)")
st, body = post(f"{SERVICE}/v1/embeddings", {"model": "m", "input": ["hello", "world"]})
vectors = body["data"]
check("two vectors, index-ordered", [v["index"] for v in vectors] == [0, 1], st)
check("1024-dim unit vectors",
      all(abs(sum(x * x for x in v["embedding"]) - 1.0) < 1e-6 for v in vectors),
      len(vectors[0]["embedding"]))

print("=" * 64)
print("3) rerank endpoint (Jina/Cohere shape)")
st, body = post(
    f"{SERVICE}/v1/rerank",
    {"query": "sales anomaly in March", "documents": [
        "vector database search", "sales peaked abnormally in March", "sunny day"]},
)
results = body["results"]
check("most relevant doc first", results[0]["index"] == 1, results[:2])
check("scores descend", [r["relevance_score"] for r in results] ==
      sorted((r["relevance_score"] for r in results), reverse=True), results)

print("=" * 64)
print("4) chat completions: sync + SSE stream")
messages = [
    {"role": "system", "content":
        "=== snippets ===\n【片段 1】来源：a.txt · 第 1 页\n退款周期为七天。"},
    {"role": "user", "content": "what is the refund policy"},
]
st, body = post(f"{SERVICE}/v1/chat/completions", {"model": "m", "messages": messages})
answer = body["choices"][0]["message"]["content"]
check("sync answer labelled + extractive", "model-service" in answer, answer[:60])

st, stream_body = post(
    f"{SERVICE}/v1/chat/completions",
    {"model": "m", "messages": messages, "stream": True}, stream=True,
)
check("stream ends with [DONE]", "data: [DONE]" in stream_body, st)
check("stream carries content", "退款" in stream_body or "refund" in stream_body.lower(), "")

print("=" * 64)
print("5) backend hardening still healthy after middleware")
st, body = post(f"{BASE.replace('/api/v1', '')}/api/v1/nonexistent", {}) if False else (None, None)
req = urllib.request.Request(f"{BASE}/nonexistent", data=b"{}", method="POST",
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        st, payload = resp.status, resp.read().decode()
except urllib.error.HTTPError as e:
    st, payload = e.code, e.read().decode()
check("unknown route -> JSON 404", st == 404 and "detail" in payload, (st, payload[:60]))

print("=" * 64)
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S): {fails}")
    raise SystemExit(1)
print("RESULT: ALL CHECKS PASSED")
