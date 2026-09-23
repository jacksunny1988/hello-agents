"""NoteTool 测试：七个动作的契约与错误码"""

import json

import pytest

from hello_agents.tools.builtin.note_tool import _ACTIONS, NoteTool
from hello_agents.tools.response import ToolStatus


@pytest.fixture
def tool(note_store) -> NoteTool:
    return NoteTool(note_store)


def test_动作集完整():
    assert _ACTIONS == {
        "create",
        "read",
        "update",
        "delete",
        "list",
        "search",
        "summary",
    }


def test_get_parameters_覆盖全部动作参数(tool):
    names = {param.name for param in tool.get_parameters()}
    assert {"action", "id", "title", "body", "type", "tags", "query", "limit"} <= names


def test_create_成功(tool):
    response = tool.run(
        {"action": "create", "title": "项目进展", "body": "正文", "tags": ["phase1"]}
    )
    assert response.status is ToolStatus.SUCCESS
    assert response.data["title"] == "项目进展"
    assert response.data["id"].startswith("note_")
    assert "已创建笔记" in response.text


def test_create_缺_title_报_invalid_param(tool):
    assert (
        tool.run({"action": "create", "body": "正文"}).error_info["code"]
        == "INVALID_PARAM"
    )


def test_read_成功含正文(tool):
    note_id = tool.run(
        {"action": "create", "title": "标题", "body": "## 结论\n\n锁定 httpx<0.28。"}
    ).data["id"]
    response = tool.run({"action": "read", "id": note_id})
    assert response.status is ToolStatus.SUCCESS
    assert response.data["body"] == "## 结论\n\n锁定 httpx<0.28。"
    assert "锁定 httpx<0.28。" in response.text


def test_read_不存在报_not_found(tool):
    assert (
        tool.run({"action": "read", "id": "note_missing"}).error_info["code"]
        == "NOT_FOUND"
    )


def test_read_缺_id_报_invalid_param(tool):
    assert tool.run({"action": "read"}).error_info["code"] == "INVALID_PARAM"


def test_update_成功(tool):
    note_id = tool.run({"action": "create", "title": "标题", "body": "旧"}).data["id"]
    assert (
        tool.run({"action": "update", "id": note_id, "body": "新"}).status
        is ToolStatus.SUCCESS
    )
    assert tool.run({"action": "read", "id": note_id}).data["body"] == "新"


def test_update_无字段报_invalid_param(tool):
    note_id = tool.run({"action": "create", "title": "标题"}).data["id"]
    assert (
        tool.run({"action": "update", "id": note_id}).error_info["code"]
        == "INVALID_PARAM"
    )


def test_update_不存在报_not_found(tool):
    response = tool.run({"action": "update", "id": "note_missing", "body": "x"})
    assert response.error_info["code"] == "NOT_FOUND"


def test_delete_成功与不存在(tool):
    note_id = tool.run({"action": "create", "title": "标题"}).data["id"]
    assert tool.run({"action": "delete", "id": note_id}).data["deleted"] is True
    response = tool.run({"action": "delete", "id": note_id})
    assert response.status is ToolStatus.SUCCESS
    assert response.data["deleted"] is False


def test_list_过滤(tool):
    tool.run({"action": "create", "title": "阻塞", "type": "blocker"})
    tool.run({"action": "create", "title": "决策", "type": "decision"})
    response = tool.run({"action": "list", "type": "blocker"})
    assert [note["title"] for note in response.data["notes"]] == ["阻塞"]
    assert "阻塞" in response.text


def test_list_空库返回提示(tool):
    response = tool.run({"action": "list"})
    assert response.data["notes"] == []
    assert response.text == "暂无笔记。"


def test_search_返回_score(tool):
    tool.run({"action": "create", "title": "依赖冲突排查", "body": "依赖冲突"})
    hits = tool.run({"action": "search", "query": "依赖冲突"}).data["hits"]
    assert hits[0]["title"] == "依赖冲突排查"
    assert hits[0]["score"] == 1.0


def test_search_缺_query_报_invalid_param(tool):
    assert tool.run({"action": "search"}).error_info["code"] == "INVALID_PARAM"


def test_summary_返回_sections(tool):
    tool.run(
        {"action": "create", "title": "项目进展", "body": "## 完成情况\n\n已完成重构。"}
    )
    notes = tool.run({"action": "summary"}).data["notes"]
    assert notes[0]["sections"] == [{"heading": "完成情况", "preview": "已完成重构。"}]


def test_未知_action_报_invalid_param(tool):
    assert tool.run({"action": "rewrite"}).error_info["code"] == "INVALID_PARAM"


def test_纯文本入参默认_search(tool):
    tool.run({"action": "create", "title": "依赖冲突排查"})
    assert tool.run("依赖冲突").data["hits"][0]["title"] == "依赖冲突排查"


def test_json_字符串入参(tool):
    assert (
        tool.run(json.dumps({"action": "create", "title": "标题"})).status
        is ToolStatus.SUCCESS
    )


def test_tags_传字符串时按逗号与空格切分(tool):
    note_id = tool.run(
        {"action": "create", "title": "标题", "tags": "deps, phase1"}
    ).data["id"]
    assert tool.run({"action": "read", "id": note_id}).data["tags"] == [
        "deps",
        "phase1",
    ]


def test_磁盘故障报_note_error(tool, monkeypatch):
    def boom(note_id):
        raise OSError("磁盘满了")

    monkeypatch.setattr(tool.store, "read", boom)
    assert (
        tool.run({"action": "read", "id": "note_x"}).error_info["code"] == "NOTE_ERROR"
    )
