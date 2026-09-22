"""RAG 管道测试：ingest → query 闭环（TF-IDF 路径）"""

import pytest

from hello_agents.memory.rag import RAGPipeline


@pytest.fixture
def pipeline(manager):
    pipe = RAGPipeline(memory_manager=manager, chunk_size=200, chunk_overlap=20)
    yield pipe
    pipe.close()


def test_ingest_text_and_query_hits_relevant_chunk(pipeline):
    count = pipeline.ingest_text(
        "HelloAgents 是一个支持 ReAct 模式的智能体框架。" * 5,
        source="intro",
    )
    assert count >= 1

    result = pipeline.query("什么是 HelloAgents", generate=False)

    assert result.generated is False
    assert "HelloAgents" in result.answer
    assert result.chunks


def test_ingest_file_roundtrip(pipeline, tmp_path):
    doc = tmp_path / "product.md"
    doc.write_text("产品定价为每月 99 元，含 24 小时支持。", encoding="utf-8")

    assert pipeline.ingest_file(doc) == 1

    result = pipeline.query("产品多少钱", generate=False)
    assert "99 元" in result.answer


def test_query_without_corpus_returns_hint(pipeline):
    result = pipeline.query("宇宙的尽头在哪里", generate=False)
    assert "未检索到" in result.answer
    assert result.chunks == []


def test_llm_generation_path(manager):
    prompts = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "根据资料回答：答案是 42。"

    pipeline = RAGPipeline(memory_manager=manager, llm=fake_llm)
    pipeline.ingest_text("答案是 42。")
    result = pipeline.query("答案是什么")

    assert result.generated is True
    assert result.answer == "根据资料回答：答案是 42。"
    assert prompts and "## 上下文" in prompts[0]


def test_llm_skipped_when_no_hits(manager):
    pipeline = RAGPipeline(memory_manager=manager, llm=lambda p: "不应被调用")
    result = pipeline.query("没有相关资料", generate=True)

    assert result.generated is False
    assert "未检索到" in result.answer
