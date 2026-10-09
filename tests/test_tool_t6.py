"""T6：内置文件/搜索工具（read_file · write_file · glob · grep），全部离线确定性"""

from pathlib import Path

import pytest

from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._response import ToolStatus
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool.builtin._fs import ReadFileTool, WriteFileTool
from hello_agents.tool.builtin._search import GlobTool, GrepTool

A_PY = """\
import os
from pathlib import Path


def foo():
    return 1


def bar():
    return 2
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """a.py（10 行，第 5 行是 def foo）· b.txt · sub/c.py"""
    (tmp_path / "a.py").write_text(A_PY, encoding="utf-8")
    (tmp_path / "b.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    return tmp_path


# --- read_file --------------------------------------------------------------


async def test_read_file_numbers_lines_from_one(tree: Path):
    resp = await ReadFileTool()(file_path=str(tree / "a.py"))
    assert resp.status is ToolStatus.SUCCESS
    first = resp.get_text().splitlines()[0]
    assert first.split("\t") == ["     1", "import os"]
    assert "def foo():" in resp.get_text()


async def test_read_file_offset_and_limit_slice_lines(tree: Path):
    resp = await ReadFileTool()(file_path=str(tree / "a.py"), offset=2, limit=1)
    text = resp.get_text()
    assert len(text.splitlines()) == 1
    assert text.split("\t") == ["     2", "from pathlib import Path"]


async def test_read_file_offset_points_at_later_line(tree: Path):
    resp = await ReadFileTool()(file_path=str(tree / "a.py"), offset=5, limit=1)
    assert resp.get_text().split("\t") == ["     5", "def foo():"]


async def test_read_file_missing_file_returns_error(tree: Path):
    resp = await ReadFileTool()(file_path=str(tree / "nope.py"))
    assert resp.status is ToolStatus.ERROR
    assert "不存在" in resp.get_text()


async def test_read_file_on_directory_returns_error(tree: Path):
    """必须是显式 fail，而不是靠 T2 兜住 IsADirectoryError。"""
    resp = await ReadFileTool()(file_path=str(tree))
    assert resp.status is ToolStatus.ERROR
    assert "目录" in resp.get_text()


# --- write_file -------------------------------------------------------------


async def test_write_file_creates_new_file(tmp_path: Path):
    target = tmp_path / "new.txt"
    resp = await WriteFileTool()(file_path=str(target), content="hello")
    assert resp.status is ToolStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "hello"


async def test_write_file_creates_missing_parent_dirs(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "x.txt"
    resp = await WriteFileTool()(file_path=str(target), content="deep")
    assert resp.status is ToolStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "deep"


async def test_write_file_overwrites_by_default(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("old", encoding="utf-8")
    await WriteFileTool()(file_path=str(target), content="new")
    assert target.read_text(encoding="utf-8") == "new"


async def test_write_file_appends_when_asked(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("old\n", encoding="utf-8")
    await WriteFileTool()(file_path=str(target), content="new\n", append=True)
    assert target.read_text(encoding="utf-8") == "old\nnew\n"


async def test_write_file_via_toolkit_with_approver(tmp_path: Path):
    target = tmp_path / "via_toolkit.txt"
    toolkit = Toolkit(tools=[WriteFileTool()], approver=AutoApprover(True))
    resp = await toolkit.call_tool("write_file", file_path=str(target), content="ok")
    assert resp.status is ToolStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "ok"


async def test_write_file_without_approver_raises(tmp_path: Path):
    """写操作走 T5 审批：没配 approver 就必须 fail loud。"""
    toolkit = Toolkit(tools=[WriteFileTool()])
    with pytest.raises(RuntimeError, match="approver"):
        await toolkit.call_tool(
            "write_file", file_path=str(tmp_path / "x.txt"), content="x"
        )


async def test_read_file_needs_no_approver(tree: Path):
    """只读工具不该被审批拦住。"""
    toolkit = Toolkit(tools=[ReadFileTool()])
    resp = await toolkit.call_tool("read_file", file_path=str(tree / "a.py"))
    assert resp.status is ToolStatus.SUCCESS


# --- glob -------------------------------------------------------------------


async def test_glob_recursive_python_files_sorted(tree: Path):
    resp = await GlobTool()(pattern="**/*.py", path=str(tree))
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text().splitlines() == sorted(
        [str(tree / "a.py"), str(tree / "sub" / "c.py")]
    )


async def test_glob_single_star_only_top_level(tree: Path):
    resp = await GlobTool()(pattern="*.py", path=str(tree))
    assert resp.get_text().splitlines() == [str(tree / "a.py")]


async def test_glob_finds_txt(tree: Path):
    resp = await GlobTool()(pattern="*.txt", path=str(tree))
    assert resp.get_text().strip() == str(tree / "b.txt")


async def test_glob_no_match_is_not_an_error(tree: Path):
    resp = await GlobTool()(pattern="*.md", path=str(tree))
    assert resp.status is ToolStatus.SUCCESS
    assert "无匹配" in resp.get_text()


async def test_glob_missing_path_returns_error(tmp_path: Path):
    resp = await GlobTool()(pattern="*", path=str(tmp_path / "nope"))
    assert resp.status is ToolStatus.ERROR


# --- grep -------------------------------------------------------------------


async def test_grep_reports_file_line_and_content(tree: Path):
    resp = await GrepTool()(pattern="def foo", path=str(tree))
    assert resp.status is ToolStatus.SUCCESS
    text = resp.get_text()
    assert f"{tree / 'a.py'}:5:def foo():" in text
    assert f"{tree / 'sub' / 'c.py'}:1:def foo():" in text


async def test_grep_include_filters_by_filename(tree: Path):
    # hello 只在 b.txt 里；限定 *.py 应当搜不到
    resp = await GrepTool()(pattern="hello", path=str(tree), include="*.py")
    assert "无匹配内容" in resp.get_text()

    resp_txt = await GrepTool()(pattern="hello", path=str(tree), include="*.txt")
    assert str(tree / "b.txt") in resp_txt.get_text()


async def test_grep_ignore_case(tmp_path: Path):
    (tmp_path / "u.txt").write_text("HELLO World\n", encoding="utf-8")

    strict = await GrepTool()(pattern="hello", path=str(tmp_path))
    assert "无匹配内容" in strict.get_text()

    loose = await GrepTool()(pattern="hello", path=str(tmp_path), ignore_case=True)
    assert "HELLO World" in loose.get_text()


async def test_grep_files_only_lists_files_without_line_numbers(tree: Path):
    resp = await GrepTool()(pattern="def foo", path=str(tree), files_only=True)
    assert set(resp.get_text().splitlines()) == {
        str(tree / "a.py"),
        str(tree / "sub" / "c.py"),
    }


async def test_grep_invalid_regex_returns_error(tree: Path):
    resp = await GrepTool()(pattern="[unclosed", path=str(tree))
    assert resp.status is ToolStatus.ERROR
    assert "非法正则" in resp.get_text()


async def test_grep_skips_git_and_venv_dirs(tree: Path):
    for skipped in (".git", ".venv"):
        d = tree / skipped
        d.mkdir()
        (d / "x.py").write_text("SECRET_TOKEN\n", encoding="utf-8")

    resp = await GrepTool()(pattern="SECRET_TOKEN", path=str(tree))
    assert resp.status is ToolStatus.SUCCESS
    assert "无匹配内容" in resp.get_text()


async def test_grep_head_limit_caps_result_count(tmp_path: Path):
    (tmp_path / "many.txt").write_text(
        "\n".join(f"hit {i}" for i in range(10)), encoding="utf-8"
    )
    resp = await GrepTool()(pattern="hit", path=str(tmp_path), head_limit=3)
    assert len(resp.get_text().splitlines()) == 3


async def test_grep_missing_path_returns_error(tmp_path: Path):
    resp = await GrepTool()(pattern="x", path=str(tmp_path / "nope"))
    assert resp.status is ToolStatus.ERROR


async def test_grep_can_search_a_single_file(tree: Path):
    resp = await GrepTool()(pattern="def bar", path=str(tree / "a.py"))
    assert f"{tree / 'a.py'}:9:def bar():" in resp.get_text()
