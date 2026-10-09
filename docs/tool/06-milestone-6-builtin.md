# 里程碑 T6：内置文件/搜索工具（read_file · write_file · glob · grep）

> 目标：亲手实现 4 个纯 Python 内置工具，验证前面所有框架能力（统一抽象、执行收口、
> 审批、Hook）。bash 在 T7 单独做。
> 对标（只读）：`agentscope/tool/_builtin/_read.py`、`_write.py`、`_glob.py`、`_grep.py`。

---

## 6.1 设计原理（先理解）

### 1) 为什么用纯 Python，而不是 agentscope 的 backend / ripgrep / helper 脚本

agentscope 为可替换沙箱（本地/Docker/E2B）引入 `BackendBase`，glob 靠打包的
`_glob_helper.py`，grep 靠外部 `ripgrep`。我们不做沙箱、目标是吃透工具框架本身，因此：

- 文件 IO 直接用 `pathlib`；
- glob 用 `Path.glob`（原生支持 `**` 递归）；
- grep 用 `os.walk`/`Path.rglob` + `re` + `fnmatch`。

零外部二进制、跨平台、测试确定。代价是大仓库下性能不如 ripgrep——学习场景可接受，
文档末尾留"如何换成 ripgrep"的思考。

### 2) 为什么 4 个工具都写同步 `def`

文件 IO 用同步 API 最直接。同步 `call` 会被 T2 的 `asyncio.to_thread` 自动丢进线程池，
不阻塞事件循环。这正好在**真实工具**上验证 T2 的同步分支，而不只是测试替身。

### 3) read_file 为什么输出带行号

带行号（`cat -n` 风格）让模型在后续 write/edit 时能精确定位行；offset/limit 支持分段读长文件。

### 4) 错误为什么显式返回 fail

文件不存在、路径是目录、正则非法等属于"预期内错误"。显式 `ToolResponse.fail` 返回，
比依赖 T2 兜底（异常文本）给模型的信息更清晰、可控。

### 5) 写操作与 T5 审批的关系

`write_file` 有副作用：`is_read_only=False`，默认就会触发 T5 的人工确认（写操作才问）。
本里程碑只实现工具；测试里用 `AutoApprover(True)` 放行。

---

## 6.2 接口契约：填充 `builtin/_fs.py`

### read_file 字段

| 参数 | 类型 | 必填/默认 | 说明 |
|---|---|---|---|
| file_path | str | 必填 | 文件路径（相对路径相对 cwd 解析） |
| offset | int | 默认 1 | 起始行（1 基） |
| limit | int | 默认 2000 | 最多读取行数 |

### write_file 字段

| 参数 | 类型 | 必填/默认 | 说明 |
|---|---|---|---|
| file_path | str | 必填 | 目标文件路径 |
| content | str | 必填 | 写入内容 |
| append | bool | 默认 False | True=追加，False=覆盖 |

### 完整代码

```python
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

    def call(
        self, file_path: str, offset: int = 1, limit: int = 2000
    ) -> ToolResponse:
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

    def call(
        self, file_path: str, content: str, append: bool = False
    ) -> ToolResponse:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append else "w"
        with path.open(mode, encoding="utf-8") as f:
            f.write(content)

        action = "追加" if append else "写入"
        return ToolResponse.succeed(
            f"已{action} {path}（{len(content)} 字符）"
        )
```

---

## 6.3 接口契约：填充 `builtin/_search.py`

### glob 字段

| 参数 | 类型 | 必填/默认 | 说明 |
|---|---|---|---|
| pattern | str | 必填 | glob 模式（如 `**/*.py`） |
| path | str | 默认 "." | 搜索根目录 |

### grep 字段

| 参数 | 类型 | 必填/默认 | 说明 |
|---|---|---|---|
| pattern | str | 必填 | 正则表达式 |
| path | str | 默认 "." | 文件或目录 |
| include | str | 默认 "*" | 文件名 glob 过滤（如 `*.py`） |
| ignore_case | bool | 默认 False | 忽略大小写 |
| files_only | bool | 默认 False | 只输出含匹配的文件路径 |
| head_limit | int | 默认 250 | 结果条数上限 |

### 完整代码

```python
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

        matches = sorted(
            {p for p in root.glob(pattern) if p.is_file()}
        )
        if not matches:
            return ToolResponse.succeed(f"无匹配文件：{pattern}")
        return ToolResponse.succeed("\n".join(str(p) for p in matches))


class GrepTool(ToolBase):
    name = "grep"
    description = "用正则搜索文件内容，返回 文件:行号:匹配行；可按文件名过滤、忽略大小写。"
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
            regex = re.compile(
                pattern, re.IGNORECASE if ignore_case else 0
            )
        except re.error as exc:
            return ToolResponse.fail(f"非法正则：{exc}")

        results: list[str] = []
        matched_files: set[Path] = set()
        for file in _iter_files(root):
            if not fnmatch.fnmatch(file.name, include):
                continue
            try:
                lines = file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
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
            return ToolResponse.succeed(
                "\n".join(output) if output else "无匹配文件"
            )
        return ToolResponse.succeed(
            "\n".join(results) if results else "无匹配内容"
        )
```

> 裁剪：不做 output_mode=count、context(-A/-B/-C)、type、multiline；默认跳过
> `.git/.venv/__pycache__` 等目录。

---

## 6.4 留给你的动手任务

### 1) 填充 `_fs.py`、`_search.py`。

### 2) 写测试 `tests/test_tool_t6.py`，用 pytest 的 `tmp_path` 造临时文件树（离线、确定）：

建议文件树：`a.py`（内容多行含 "def foo"）、`b.txt`、`sub/c.py`。

- **read_file**
  - 正常读取，行号从 1 开始、含内容；
  - offset/limit 切片正确（如 offset=2,limit=1 只返回第 2 行）；
  - 不存在文件 → ERROR、文本含"不存在"；
  - 传目录 → ERROR；
- **write_file**
  - 写新文件后磁盘内容一致；父目录不存在会自动创建；
  - 覆盖已有文件；append=True 在原内容后追加；
  - 经 Toolkit + AutoApprover(True) 调用成功；无 approver 时 write_file 触发 RuntimeError
    （证明写操作走了 T5 审批）；
- **glob**
  - pattern `**/*.py` 返回 `a.py` 与 `sub/c.py`（排序、用相对/绝对一致口径）；
  - pattern `*.txt` 返回 b.txt；无匹配返回"无匹配"；
  - path 不存在 → ERROR；
- **grep**
  - pattern `def foo` content 模式命中，输出含文件名、行号、该行内容；
  - include="*.py" 时只搜 py；ignore_case 命中大写；
  - files_only=True 只输出文件；非法正则 → ERROR；
  - 不搜 `.git`/`.venv`（可在 tmp_path 下造一个 .git/x.py 验证被跳过）。

> 提示：这些工具是同步 call，测试可直接 `await Toolkit(...).call_tool(...)`（T2 自动
> to_thread）；也可直接实例化工具、`await tool(**kwargs)`。

### 3) 写演示 `examples/tool_t6_builtin.py`：

- glob `**/*.py`（path 设为项目目录）；
- grep 某个关键词（如 "class Toolkit"，include="*.py"）；
- read_file 读一个文件前几行；
- write_file 写到临时目录（配 AutoApprover(True)，避免交互）。

---

## 6.5 验收清单

- [ ] `_fs.py` / `_search.py` 与契约一致；
- [ ] test_tool_t6 全绿（含写操作审批、glob 排序、grep 各模式、跳过 .git）；
- [ ] T1–T5 回归全绿；
- [ ] ruff 干净：

```powershell
uv run ruff check hello_agents/tool tests/test_tool_t6.py examples/tool_t6_builtin.py
uv run ruff format --check hello_agents/tool tests/test_tool_t6.py examples/tool_t6_builtin.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py tests/test_tool_t6.py -q
```

---

## 6.6 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| ReadFileTool | `_builtin/_read.py` Read | cat -n、offset/保留；裁剪绝对路径强制/图片/PDF/缓存 |
| WriteFileTool | `_builtin/_write.py` Write | 覆盖/父目录创建；裁剪 diff/危险路径/先读后写；新增 append |
| GlobTool | `_builtin/_glob.py` Glob | Path.glob 替代 helper 脚本；排序改为路径序（agentscope 按 mtime） |
| GrepTool | `_builtin/_grep.py` Grep | re 替代 ripgrep；裁剪 context/type/multiline/count |

> 思考题：grep 若要换成 ripgrep，可 `asyncio.create_subprocess_exec("rg", ...)`，
> 输出解析与 T7 bash 的子进程收口是同一套机制。

---

## 完成后

把 `_fs.py`、`_search.py`、tool_t6 输出与 pytest 结果贴给我 review。
通过后进入 **T7：bash 工具（命令构造 · 子进程执行 · 超时/审批，分步实现）**。
