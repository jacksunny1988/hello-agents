"""RAGTool 测试：ingest / query 行为"""

import pytest

from hello_agents.memory.rag import RAGPipeline
from hello_agents.tools.builtin import RAGTool
from hello_agents.tools.response import ToolStatus


@pytest.fixture
def tool(manager):
    pipeline = RAGPipeline(memory_manager=manager, chunk_size=200, chunk_overlap=20)
    return RAGTool(pipeline=pipeline)


def test_ingest_text_and_query(tool):
    resp = tool.run({"action": "ingest", "content": "退款政策是 7 天无理由退货。"})
    assert resp.status == ToolStatus.SUCCESS
    assert resp.data["chunk_count"] == 1

    resp = tool.run({"action": "query", "question": "退款政策是什么"})
    assert resp.status == ToolStatus.SUCCESS
    assert "7 天" in resp.text
    assert resp.data["generated"] is False


def test_ingest_file(tool, tmp_path):
    doc = tmp_path / "faq.txt"
    doc.write_text("客服邮箱是 support@example.com。", encoding="utf-8")

    resp = tool.run({"action": "ingest", "source": str(doc)})
    assert resp.status == ToolStatus.SUCCESS
    assert resp.data["chunk_count"] == 1

    resp = tool.run({"action": "query", "question": "客服邮箱", "top_k": 2})
    assert "support@example.com" in resp.text


def test_plain_text_defaults_to_query(tool):
    tool.run({"action": "ingest", "content": "系统每周日凌晨自动备份。"})
    resp = tool.run("什么时候备份")
    assert resp.status == ToolStatus.SUCCESS
    assert "备份" in resp.text


def test_query_empty_corpus(tool):
    resp = tool.run({"action": "query", "question": "任意问题"})
    assert resp.status == ToolStatus.SUCCESS
    assert "未检索到" in resp.text


def test_ingest_without_source_or_content_is_error(tool):
    resp = tool.run({"action": "ingest"})
    assert resp.status == ToolStatus.ERROR
    assert resp.error_info["code"] == "INVALID_PARAM"


def test_unknown_action_is_error(tool):
    resp = tool.run({"action": "train"})
    assert resp.status == ToolStatus.ERROR
    assert "未知 action" in resp.error_info["message"]


def test_missing_question_is_error(tool):
    resp = tool.run({"action": "query"})
    assert resp.status == ToolStatus.ERROR
    assert "question" in resp.error_info["message"]


def test_get_parameters_signature(tool):
    names = [param.name for param in tool.get_parameters()]
    assert names == ["action", "source", "content", "question", "top_k"]


def test_query_chunks_include_score(tool):
    """修复 B15：chunk 字典必须带 score，与 MemoryTool.recall 对齐

    注意：ingest 只传 `content`，**不要**同时传 `source` —— `rag_tool.py` 的分派是
    `if source: ingest_file(source) / elif content: ingest_text(content)`，source 优先，
    `"demo"` 会被当成文件路径走 `ingest_file` 而失败，query 拿到 0 个 chunk，
    与本用例要钉的 score 无关地变红。
    """
    tool.run({"action": "ingest", "content": "向量库使用 Qdrant 存储"})

    # 不只钉键存在 —— `{"score": 0.0}` 也能满足 `"score" in chunk`。
    # 与**同一次** pipeline.query 的检索真值比对，钉住 score 是真带过来的，不是占位。
    # 真值必须取自同一次检索：TFIDFEmbedding.embed_texts 会把 query 也计入文档频次，
    # IDF 随每次查询漂移（实测同串连查两次 0.672 vs 0.695），跨次比对会无关地变红。
    captured: list = []
    original_query = tool.pipeline.query

    def capture_query(question, **kwargs):
        result = original_query(question, **kwargs)
        captured.append(result)
        return result

    tool.pipeline.query = capture_query
    resp = tool.run({"action": "query", "question": "向量库用什么存储"})
    result = captured[0]

    assert resp.data["chunks"]
    # zip 静默截断防护：两侧长度不等时 zip 会悄悄少比，必须先对称断言
    assert len(resp.data["chunks"]) == len(result.chunks)
    for chunk, item in zip(resp.data["chunks"], result.chunks):
        assert chunk["score"] == pytest.approx(item.score)
