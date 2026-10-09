from pathlib import Path
from typing import Any, ClassVar

from .._base import ToolBase
from .._response import ToolResponse


class ReadFileTool(ToolBase):
    name = "read_file"
    description = "读取本地文本文件，返回带行号的内容，支持 offset/limit 分段读取。"
    is_read_only = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "要读取的文件路径"},
            "offset": {
                "type": "integer",
                "description": "起始行号（1 基），默认 1",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "最多读取行数，默认 2000",
                "default": 2000,
                "minimum": 1,
            },
        },
        "required": ["file_path"],
    }

    def call(self, file_path: str, offset: int = 1, limit: int = 2000) -> ToolResponse:
        path = Path(file_path).expanduser()

        if not path.exists():
            return ToolResponse.fail(f"文件不存在：{path}")
        if path.is_dir():
            return ToolResponse.fail(f"路径是目录而非文件：{path}")

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]

        formatted = [
            f"{line_no:6d}\t{content}"
            for line_no, content in enumerate(selected, start=offset)
        ]
        return ToolResponse.succeed("\n".join(formatted))


class WriteFileTool(ToolBase):
    name = "write_file"
    description = "写入文本文件（默认覆盖，append=True 为追加），自动创建父目录。"
    is_read_only = False
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "要写入的文本"},
            "append": {
                "type": "boolean",
                "description": "是否追加，默认 false（覆盖）",
                "default": False,
            },
        },
        "required": ["file_path", "content"],
    }

    def call(self, file_path: str, content: str, append: bool = False) -> ToolResponse:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as f:
            f.write(content)

        action = "追加" if append else "写入"
        return ToolResponse.succeed(f"已{action} {path}（{len(content)} 字符）")
