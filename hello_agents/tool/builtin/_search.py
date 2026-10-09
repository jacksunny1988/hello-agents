import fnmatch
import re
from pathlib import Path
from typing import Any, ClassVar

from .._base import ToolBase
from .._response import ToolResponse

_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "node_modules"}


def _iter_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and not (set(p.parts) & _SKIP_DIRS):
            files.append(p)
    return files


class GlobTool(ToolBase):
    name = "glob"
    description = "按 glob 模式查找文件路径（支持 ** 递归），返回排序后的路径列表。"
    is_read_only = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 模式，如 **/*.py",
            },
            "path": {
                "type": "string",
                "description": "搜索根目录，默认当前目录",
                "default": ".",
            },
        },
        "required": ["pattern"],
    }

    def call(self, pattern: str, path: str = ".") -> ToolResponse:
        root = Path(path).expanduser()
        if not root.exists():
            return ToolResponse.fail(f"搜索路径不存在：{root}")

        matches = sorted({p for p in root.glob(pattern) if p.is_file()})
        if not matches:
            return ToolResponse.succeed(f"无匹配文件：{pattern}")
        return ToolResponse.succeed("\n".join(str(p) for p in matches))


class GrepTool(ToolBase):
    name = "grep"
    description = (
        "用正则搜索文件内容，返回 文件:行号:匹配行；可按文件名过滤、忽略大小写。"
    )
    is_read_only = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {
                "type": "string",
                "description": "文件或目录，默认当前目录",
                "default": ".",
            },
            "include": {
                "type": "string",
                "description": "文件名 glob 过滤，如 *.py，默认 *",
                "default": "*",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "忽略大小写，默认 false",
                "default": False,
            },
            "files_only": {
                "type": "boolean",
                "description": "只输出含匹配的文件路径，默认 false",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": "结果条数上限，默认 250",
                "default": 250,
                "minimum": 1,
            },
        },
        "required": ["pattern"],
    }

    def call(
        self,
        pattern: str,
        path: str = ".",
        include: str = "*",
        ignore_case: bool = False,
        files_only: bool = False,
        head_limit: int = 250,
    ) -> ToolResponse:
        root = Path(path).expanduser()
        if not root.exists():
            return ToolResponse.fail(f"搜索路径不存在：{root}")

        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return ToolResponse.fail(f"非法正则：{exc}")

        results: list[str] = []
        matched_files: set[Path] = set()
        for file in _iter_files(root):
            if not fnmatch.fnmatch(file.name, include):
                continue
            try:
                lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, start=1):
                if regex.search(line):
                    matched_files.add(file)
                    if not files_only:
                        results.append(f"{file}:{line_no}:{line}")
                        if len(results) >= head_limit:
                            return ToolResponse.succeed("\n".join(results))

        if files_only:
            output = sorted(str(f) for f in matched_files)
            return ToolResponse.succeed("\n".join(output) if output else "无匹配文件")
        return ToolResponse.succeed("\n".join(results) if results else "无匹配内容")
