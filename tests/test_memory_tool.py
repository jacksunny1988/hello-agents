"""MemoryTool 测试：remember / recall / forget + ToolResponse 状态"""

import json

import pytest

from hello_agents.tools.builtin import MemoryTool
from hello_agents.tools.response import ToolStatus as ResponseStatus


@pytest.fixture
def tool(manager):
    return MemoryTool(manager=manager)


def test_remember_and_recall_roundtrip(tool):
    resp = tool.run(
        {"action": "remember", "content": "用户喜欢深蓝色", "memory_type": "semantic"}
    )
    assert resp.status == ResponseStatus.SUCCESS
    assert resp.data["memory_type"] == "semantic"

    resp = tool.run({"action": "recall", "query": "用户喜欢什么颜色"})
    assert resp.status == ResponseStatus.SUCCESS
    assert "深蓝色" in resp.text


def test_remember_accepts_json_string(tool):
    payload = json.dumps(
        {"action": "remember", "content": "会议在周五 10 点"}, ensure_ascii=False
    )
    resp = tool.run(payload)
    assert resp.status == ResponseStatus.SUCCESS
    assert resp.data["id"]


def test_plain_text_defaults_to_recall(tool):
    tool.run({"action": "remember", "content": "部署流程见 runbook"})
    resp = tool.run("runbook")
    assert resp.status == ResponseStatus.SUCCESS
    assert "runbook" in resp.text


def test_recall_empty_returns_message(tool):
    resp = tool.run({"action": "recall", "query": "从未提及的话题"})
    assert resp.status == ResponseStatus.SUCCESS
    assert "未检索到" in resp.text
    assert resp.data["hits"] == []


def test_forget_removes_item(tool):
    resp = tool.run({"action": "remember", "content": "临时凭证 abc123"})
    item_id = resp.data["id"]

    resp = tool.run({"action": "forget", "id": item_id})
    assert resp.status == ResponseStatus.SUCCESS
    assert resp.data["deleted"] is True

    resp = tool.run({"action": "forget", "id": item_id})
    assert resp.data["deleted"] is False


def test_missing_content_on_remember_is_error(tool):
    resp = tool.run({"action": "remember"})
    assert resp.status == ResponseStatus.ERROR
    assert resp.error_info["code"] == "INVALID_PARAM"


def test_unknown_action_is_error(tool):
    resp = tool.run({"action": "destroy"})
    assert resp.status == ResponseStatus.ERROR
    assert "未知 action" in resp.error_info["message"]


def test_unknown_memory_type_is_error(tool):
    resp = tool.run(
        {"action": "remember", "content": "x", "memory_type": "holographic"}
    )
    assert resp.status == ResponseStatus.ERROR
    assert "未知记忆类型" in resp.error_info["message"]


def test_get_parameters_covers_required_actions(tool):
    names = [param.name for param in tool.get_parameters()]
    assert names == ["action", "content", "query", "memory_type", "limit"]
    assert tool.get_parameters()[0].required is True
