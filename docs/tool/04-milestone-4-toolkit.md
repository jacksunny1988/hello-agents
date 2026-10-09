# 里程碑 T4：Toolkit / 分组（注册 · 查找 · 分组 · 导出 schema · 单工具调用）

> 目标：实现工具的统一注册中心 `Toolkit`——注册类工具/函数工具（MCP 工具 T8 也进这里）、
> 按名查找、注销、分组过滤、批量导出 function schemas、单工具调用收口。
> 对标（只读）：`agentscope/tool/_toolkit.py`、`agentscope/tool/_tool_group.py`、
> `agentscope/tool/_types.py` 的 `RegisteredTool`。

---

## 4.1 设计原理（先理解）

### 1) Toolkit 的角色：注册中心 + 门面（Facade）

Agent/模型不应自己维护"有哪些工具、叫什么、schema 是什么"。Toolkit 集中收口：

- **注册**：工具对象登记入册，名字唯一；
- **查找**：模型给出工具名 → 取到工具实例；
- **导出**：一次性产出给模型的 `tools` schema 数组；
- **调用**：按名调用，复用 T2 的执行收口（线程池/超时/异常）。

上层只和 Toolkit 打交道，不直接持有散落的工具。

### 2) 为什么保留"分组"，但不做 agentscope 的动态激活

agentscope 的 `ToolGroup` 很重：每组含 tools/mcps/skills/instructions，还配一个 meta 工具
`ResetTools` 让模型在运行时激活/停用整组（为"大量工具按需加载、省上下文"设计）。

我们当前工具数量少、目标是吃透核心，因此**把分组降级为一个标签**：
- 每个工具有 `group`（默认 `"basic"`）；
- 可按组过滤 `list_tools` / `get_tool_schemas`；
- **不做**运行时激活/停用、不做 ResetTools、不做 skills/instructions。

这样你学到分组的本质（命名空间 + 过滤），又不被 meta 工具复杂度拖住。将来要做
"按需加载"，再升级为完整 ToolGroup 即可。

### 3) 为什么用 RegisteredTool 包一层

注册时除了工具对象，还要记录**管理信息**（group；agentscope 还有 original_name、
extended_model）。用 `RegisteredTool` dataclass 持有 `tool + group`，把"工具本身"与
"它在注册中心里的元信息"分开——这和 model 层 ModelCard 与实例分离是同一思路。

### 4) 为什么内部用 OrderedDict

用 `OrderedDict[name, RegisteredTool]`：
- 名字唯一、O(1) 查找；
- **保留注册顺序**，导出 schema 顺序稳定 → 测试与快照确定、也利于模型侧 prompt 缓存。

### 5) 重名 / 未注册为何要"大声报错"

- 重名注册：多半是 bug（两个工具撞名），默认直接 ValueError，要求显式先注销；
- 查找/调用未注册名字：KeyError，且错误信息带名字，避免静默拿到 None。

---

## 4.2 接口契约：在 `_types.py` 增加 `RegisteredTool`

在现有 `ToolStatus` 之后追加：

```python
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._base import ToolBase


@dataclass
class RegisteredTool:
    """工具 + 它在注册中心里的管理信息。"""

    tool: "ToolBase"
    group: str = "basic"
```

> `tool` 仅在 TYPE_CHECKING 下引用 ToolBase（字符串注解），`_types.py` 运行时不 import
> `_base`，避免循环导入。

---

## 4.3 接口契约：新建 `_toolkit.py`

### 方法说明

| 方法 | 入参 | 返回 | 职责 |
|---|---|---|---|
| `__init__` | tools?: list[ToolBase] | - | 建 OrderedDict，批量注册 |
| `register_tool` | tool, *, group="basic" | None | 登记工具；重名报错 |
| `register_function` | func, *, group, **kw | FunctionTool | 包装普通函数并注册 |
| `unregister` | name | None | 注销；不存在报错 |
| `get_tool` | name | ToolBase | 按名查找；不存在报错 |
| `list_tools` | *, groups=None | list[ToolBase] | 列出（可按组过滤），保序 |
| `get_tool_schemas` | *, groups=None | list[dict] | 批量导出 function schema |
| `list_groups` | - | list[str] | 组名（按首次出现顺序） |
| `call_tool` | name, **kwargs | ToolResponse | 按名调用（经 T2 收口） |

### 完整代码

```python
import asyncio
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from ._adapters import FunctionTool
from ._base import ToolBase
from ._response import ToolResponse
from ._types import RegisteredTool


class Toolkit:
    """工具注册中心 + 门面。"""

    def __init__(self, tools: list[ToolBase] | None = None) -> None:
        self._tools: OrderedDict[str, RegisteredTool] = OrderedDict()
        for tool in tools or []:
            self.register_tool(tool)

    # --- 注册 / 注销 -------------------------------------------------------

    def register_tool(self, tool: ToolBase, *, group: str = "basic") -> None:
        """登记一个工具实例。重名直接报错（需先 unregister）。"""
        if tool.name in self._tools:
            raise ValueError(f"工具 {tool.name!r} 已注册，请勿重复注册")
        self._tools[tool.name] = RegisteredTool(tool=tool, group=group)

    def register_function(
        self,
        func: Callable,
        *,
        group: str = "basic",
        **adapter_kwargs: Any,
    ) -> FunctionTool:
        """把普通函数包装成 FunctionTool 并注册，返回该工具。"""
        tool = FunctionTool(func, **adapter_kwargs)
        self.register_tool(tool, group=group)
        return tool

    def unregister(self, name: str) -> None:
        """按名注销；不存在报错。"""
        if name not in self._tools:
            raise KeyError(f"无法注销，工具 {name!r} 未注册")
        del self._tools[name]

    # --- 查找 / 列表 -------------------------------------------------------

    def get_tool(self, name: str) -> ToolBase:
        """按名取工具；不存在报错。"""
        record = self._tools.get(name)
        if record is None:
            raise KeyError(f"工具 {name!r} 未注册")
        return record.tool

    def _records(self, groups: list[str] | None) -> list[RegisteredTool]:
        if groups is None:
            return list(self._tools.values())
        group_set = set(groups)
        return [r for r in self._tools.values() if r.group in group_set]

    def list_tools(self, *, groups: list[str] | None = None) -> list[ToolBase]:
        """列出工具，可按组过滤，保留注册顺序。"""
        return [record.tool for record in self._records(groups)]

    def get_tool_schemas(
        self, *, groups: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """批量导出 OpenAI function schema，可按组过滤，顺序稳定。"""
        return [tool.get_function_schema() for tool in self.list_tools(groups=groups)]

    def list_groups(self) -> list[str]:
        """返回所有组名，按组首次出现的顺序。"""
        groups: list[str] = []
        for record in self._tools.values():
            if record.group not in groups:
                groups.append(record.group)
        return groups

    # --- 调用 --------------------------------------------------------------

    async def call_tool(self, name: str, **kwargs: Any) -> ToolResponse:
        """按名调用工具；执行收口（线程池/超时/异常）由 T2 的 __call__ 负责。"""
        tool = self.get_tool(name)
        return await tool(**kwargs)
```

> 批量并行（多个工具同时执行）不放进 Toolkit：examples 主循环用 `asyncio.gather`
> 并发调用 `call_tool`（你在 M6 已实现过）。是否并行可参考工具的
> `is_concurrency_safe` 标志（当前最小实现不强制串行化）。

---

## 4.4 留给你的动手任务

### 1) 在 `_types.py` 追加 RegisteredTool，新建并填充 `_toolkit.py`。

### 2) 写演示 `examples/tool_t4_toolkit.py`：

```python
import asyncio

from hello_agents.tool._base import ToolBase
from hello_agents.tool._toolkit import Toolkit
from hello_agents.tool._response import ToolResponse
from typing import ClassVar


class EchoTool(ToolBase):
    name = "echo"
    description = "回显"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    is_read_only = True

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


def add(a: int, b: int = 1) -> int:
    """两数相加。

    Args:
        a: 第一个数。
        b: 第二个数。
    """
    return a + b


async def main() -> None:
    echo = EchoTool()
    toolkit = Toolkit(tools=[echo])
    toolkit.register_function(add, group="math", is_read_only=True)

    print(toolkit.list_groups())                 # ['basic', 'math']
    print([t.name for t in toolkit.list_tools()])  # ['echo', 'add']
    print([t.name for t in toolkit.list_tools(groups=["math"])])  # ['add']
    print(len(toolkit.get_tool_schemas()))      # 2
    print((await toolkit.call_tool("echo", text="hi")).get_text())
    print((await toolkit.call_tool("add", a=2)).get_text())  # 3（b 默认 1）


asyncio.run(main())
```

### 3) 写测试 `tests/test_tool_t4.py`（离线、确定性）：

- **构造批量注册**：`Toolkit(tools=[...])` 后 list_tools 含这些工具；
- **register_tool + get_tool**：get_tool 返回同一实例；
- **register_function**：普通函数被包装、可经 call_tool 调用成功；
- **重名注册 → ValueError**；
- **unregister**：注销后 get_tool 报 KeyError；注销不存在名字 → KeyError；
- **get_tool 未注册 → KeyError**，错误信息含名字；
- **顺序稳定**：list_tools / get_tool_schemas 顺序 == 注册顺序；
- **分组**：默认 group=basic；不同组 list_tools(groups=...) 正确过滤；list_groups 正确；
- **get_tool_schemas**：每项是 function schema、可按组过滤；
- **call_tool 成功**：返回 ToolResponse、文本正确；默认值参数可省略；
- **call_tool 经 T2 收口**：注册一个会抛异常的工具，call_tool 返回 status=ERROR
  （而不是抛出）；
- **注销后可用同名重新注册**。

---

## 4.5 边界与裁剪

- **不做 ToolGroup 动态激活/停用**、不做 ResetTools/SkillViewer meta 工具、不做 skills；
- **不做 extended_model 动态 schema 扩展**（agentscope RegisteredTool 的能力，裁剪）；
- **不做批量并行执行**（留 examples，用 asyncio.gather）；
- 组过滤是**纯包含匹配**：`groups=["basic"]` 只返回 basic；不传 groups 返回全部；
- 空 Toolkit 合法：list_tools/get_tool_schemas 返回 []，list_groups 返回 []。

---

## 4.6 验收清单

- [ ] `_types.py` 含 RegisteredTool；`_toolkit.py` 与契约一致；
- [ ] tool_t4 演示输出正确；
- [ ] test_tool_t4 全绿（含重名、注销、分组、顺序、call_tool 收口）；
- [ ] T1/T2/T3 回归全绿；
- [ ] ruff 干净：

```powershell
uv run ruff check hello_agents/tool examples/tool_t4_toolkit.py tests/test_tool_t4.py
uv run ruff format --check hello_agents/tool examples/tool_t4_toolkit.py tests/test_tool_t4.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py -q
```

---

## 4.7 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| RegisteredTool(tool, group) | `_types.py:24-153` | 保留 tool/group；裁剪 extended_model/original_name/get_tool_schema 合并逻辑 |
| Toolkit.register_tool/function | `_toolkit.py` 构造 + ToolGroup | 我们扁平注册；agentscope 挂到组 |
| list_tools / get_tool_schemas | `_toolkit.py:171-223` | 我们同步、按 group 标签过滤；agentscope 按"激活组"（async） |
| call_tool（单工具） | `_toolkit.py:225-388` | 我们复用 T2 收口；不做流式 chunk 累加 |
| group 字符串标签 | `_tool_group.py` ToolGroup | 降级为标签；裁剪动态激活/instructions/skills/mcps |

---

## 完成后

把更新后的 `_types.py`、`_toolkit.py`、tool_t4 演示输出与 pytest 结果贴给我 review。
通过后进入 **T5：横切治理（前后 hook · 人工确认/审批 · 工具层重试）**。
