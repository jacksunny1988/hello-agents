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
