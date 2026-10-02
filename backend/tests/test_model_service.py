"""Phase 7: model-service contract tests (OpenAI-compatible stub)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SERVICE_ROOT = BACKEND_ROOT.parent / "model-service"


@pytest.fixture(scope="module")
def service_client() -> TestClient:
    spec = importlib.util.spec_from_file_location(
        "model_service_main", SERVICE_ROOT / "main.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["model_service_main"] = module
    spec.loader.exec_module(module)
    return TestClient(module.app)


def test_health(service_client: TestClient) -> None:
    resp = service_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["dim"] == 1024


def test_embeddings_openai_shape_and_dim(service_client: TestClient) -> None:
    resp = service_client.post(
        "/v1/embeddings", json={"model": "m", "input": ["第一条", "second text"]}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [item["index"] for item in data] == [0, 1]
    for item in data:
        assert len(item["embedding"]) == 1024
        norm = sum(x * x for x in item["embedding"]) ** 0.5
        assert abs(norm - 1.0) < 1e-6


def test_rerank_orders_by_relevance(service_client: TestClient) -> None:
    resp = service_client.post(
        "/v1/rerank",
        json={
            "query": "三月销售额异常",
            "documents": [
                "pgvector 提供向量检索能力。",
                "三月销售额出现异常峰值。",
                "今天天气晴朗。",
            ],
        },
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["index"] == 1, "the sales chunk must rank first"
    scores = [r["relevance_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_chat_completions_sync_and_stream(service_client: TestClient) -> None:
    messages = [
        {"role": "system", "content": "=== 检索片段 ===\n【片段 1】来源：政策.txt · 第 1 页\n平台退款政策是七天内无理由全额退款。"},
        {"role": "user", "content": "退款政策是什么"},
    ]
    resp = service_client.post(
        "/v1/chat/completions", json={"model": "m", "messages": messages}
    )
    assert resp.status_code == 200
    answer = resp.json()["choices"][0]["message"]["content"]
    assert "退款" in answer and "model-service" in answer

    with service_client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "m", "messages": messages, "stream": True},
    ) as stream:
        assert stream.status_code == 200
        body = "".join(chunk for chunk in stream.iter_text())
    assert "data: [DONE]" in body
    assert "退款" in body


def test_tool_call_arguments_normalised_for_chat_template() -> None:
    """An agent's second turn replays its own tool call.

    The OpenAI wire format carries ``function.arguments`` as a JSON string, while
    Hugging Face chat templates iterate it as a mapping (``arguments | items``).
    Without normalisation the real-model backend raised
    ``TypeError: Can only get item pairs from a mapping`` and the agent loop
    died right after the tool result.
    """
    module = sys.modules["model_service_main"]
    normalise = module._normalise_tool_call

    assert normalise(
        {"function": {"name": "calculator", "arguments": '{"expression": "1+1"}'}}
    )["function"]["arguments"] == {"expression": "1+1"}
    # Unparsable or missing arguments must not reach the template as a string.
    assert normalise({"function": {"name": "x", "arguments": "not json"}})["function"][
        "arguments"
    ] == {}
    assert normalise({"function": {"name": "x", "arguments": None}})["function"][
        "arguments"
    ] == {}
    # A mapping is already the template shape and is preserved.
    assert normalise({"function": {"name": "x", "arguments": {"a": 1}}})["function"][
        "arguments"
    ] == {"a": 1}
    # The caller's dict is not mutated.
    original = {"function": {"name": "x", "arguments": '{"a": 1}'}}
    normalise(original)
    assert original["function"]["arguments"] == '{"a": 1}'
