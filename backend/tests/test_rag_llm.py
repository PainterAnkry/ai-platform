"""LLM provider behaviour: shared prompt, mock extraction, API contract."""

from __future__ import annotations

import httpx
import pytest

from app.rag.llm import (
    ContextBlock,
    LLMError,
    MockLLMProvider,
    OpenAICompatibleChatProvider,
    build_rag_messages,
)

BLOCKS = [
    ContextBlock(
        index=1,
        source="机器学习入门.pdf · 第 2 页",
        content="机器学习是人工智能的一个分支，它通过数据训练模型。"
        "深度学习则需要大量标注数据和算力。",
    ),
    ContextBlock(
        index=2,
        source="销售报告.docx · 第 1 页",
        content="本季度销售额同比增长百分之二十，三月出现异常峰值。",
    ),
]


def test_build_rag_messages_embeds_context_and_citation_rules() -> None:
    messages = build_rag_messages("什么是机器学习", BLOCKS)

    assert messages[0]["role"] == "system"
    assert "【片段 1】" in messages[0]["content"]
    assert "机器学习入门.pdf · 第 2 页" in messages[0]["content"]
    assert "不要编造" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "问题：什么是机器学习"}


def test_build_rag_messages_without_context_says_so() -> None:
    messages = build_rag_messages("问题", [])

    assert "没有检索到相关内容" in messages[0]["content"]


def test_mock_llm_answers_extractively_with_citations() -> None:
    provider = MockLLMProvider()

    answer = provider.rag_answer("三月销售额情况如何", BLOCKS)

    assert "离线 mock" in answer, "mock output must be labelled as such"
    assert "异常峰值" in answer, "extractive answer must quote the source"
    assert "【片段 2】" in answer, "citation must point at the sales block"
    assert "来源：" in answer


def test_mock_llm_reports_no_evidence_for_unrelated_question() -> None:
    provider = MockLLMProvider()

    answer = provider.rag_answer("量子计算的纠错码原理是什么", BLOCKS)

    assert "没有找到" in answer


def test_mock_llm_complete_is_explicit_stub() -> None:
    answer = MockLLMProvider().complete([{"role": "user", "content": "hi"}])
    assert "mock" in answer


def test_openai_chat_provider_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "机器学习是……"}}
                ]
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleChatProvider(
        api_base="https://api.example.com/v1",
        api_key="sk-test",
        model="glm-4",
        temperature=0.1,
        max_tokens=256,
    )

    answer = provider.complete(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "什么是机器学习"},
        ]
    )

    assert answer == "机器学习是……"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["payload"]["model"] == "glm-4"
    assert captured["payload"]["temperature"] == 0.1
    assert captured["payload"]["max_tokens"] == 256
    assert captured["payload"]["stream"] is False
    assert provider.signature == "openai:glm-4"


def test_openai_chat_provider_surfaces_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(url: str, **kwargs):
        return httpx.Response(429, json={"error": "rate limited"})

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatibleChatProvider(
        api_base="https://x/v1", api_key="k", model="m"
    )

    with pytest.raises(LLMError, match="429"):
        provider.complete([{"role": "user", "content": "hi"}])


def test_openai_chat_provider_requires_configuration() -> None:
    provider = OpenAICompatibleChatProvider(api_base="", api_key="", model="")
    with pytest.raises(LLMError, match="LLM_API_BASE"):
        provider.complete([{"role": "user", "content": "hi"}])
