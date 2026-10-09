# 里程碑 T5：横切治理（前后 Hook · 人工确认/审批 · 工具层重试）

> 目标：在工具执行路径上统一收口三类横切能力，且工具作者无感：
> ① 调用前后 Hook（日志/审计）；② 危险操作人工确认（human-in-the-loop），拒绝→DENIED；
> ③ 工具层重试（默认关闭）。
> 对标（只读）：`agentscope/tool/_base.py` 的 `ToolMiddlewareBase`（洋葱模型）、
> agentscope 的 permission 引擎（`PermissionBehavior` ALLOW/ASK/DENY）。我们**裁剪**
> 洋葱 middleware 与 permission/沙箱，改用更简单的 Toolkit 门面治理。

---

## 5.1 设计原理（先理解）

### 1) 治理放在哪一层：Toolkit 门面，而不是 ToolBase

当前调用链：

```
Toolkit.call_tool  →  tool(**kwargs)  →  ToolBase.__call__  →  execute_tool (T2)
```

- `ToolBase.__call__` / `execute_tool` 只负责**执行收口**（线程池/超时/异常），保持纯净；
- Hook、审批、重试属于"**怎么用工具**"的策略，放在 **Toolkit 门面**。

因此 T5 改造 `Toolkit.call_tool`，由它在 `tool(**kwargs)` 外围编排治理。
**examples 主循环必须经 `toolkit.call_tool` 调用，才能享受治理**（直接 `tool(...)`
只有 T2 收口）。这与 T4"走统一入口"的教训一致。

### 2) 为什么用"前后 Hook"而不是洋葱 middleware

agentscope 的 `ToolMiddlewareBase.on_tool_call(tool, input, next_handler)` 是洋葱模型，
`next_handler` 恒为 async generator，能流式改写——强大但要求作者理解包裹协议。

我们工具一次返回一个完整结果、不做工具流式，因此用更简单的两段式：
- `before(tool, kwargs)`：调用前运行，可返回新 kwargs 覆盖、返回 None 表示不变；
- `after(tool, kwargs, response)`：调用后运行，拿到最终结果（只读审计）。

多个 Hook 按注册顺序执行 before，after 也按同序（审计场景顺序不重要、保持简单）。
作者继承 `ToolHook`、只覆盖关心的方法即可。

### 3) 人工确认：默认"写操作才问"，并允许显式覆盖

工具需要一个三态标志 `requires_confirmation`：
- `None`（默认）= **自动**：非只读（`is_read_only=False`）需确认、只读免确认；
- `True` = 总是确认；
- `False` = 从不确认。

审批由一个可替换的 `Approver` 回调决定（返回 True 放行 / False 拒绝）：
- `AutoApprover(allow)`：自动允许或拒绝（测试与批处理用）；
- `ConsoleApprover`：CLI 打印工具名+参数、读 y/n（真人交互）。

**默认 approver 为 None**：若某工具需要确认却没配 approver，直接 `RuntimeError`
（fail loud）——绝不"无人授权就执行"，也不静默放行。拒绝时返回 `ToolResponse.denied`。

### 4) 工具层重试：默认关闭、只重试 ERROR、指数退避

- 工具类属性 `max_retries`（默认 **0**，即不重试）；
- 仅当结果 `status is ERROR` 时重试；`DENIED/INTERRUPTED` 不重试；
- 退避 `retry_backoff * 2**attempt`；
- 重试只重复 `tool(**kwargs)`，before/after 各只跑一次（视为同一次逻辑调用）。

**与 model 层重试的关系（重要）**：model 层重试的是"模型请求"，工具层重试的是
"工具执行"，对象不同；但同一回合二者叠加会放大调用量（与你 model 阶段发现的
"SDK max_retries 与基类重试叠加"同类）。所以工具层**默认 0、需显式开启**，
把是否幂等的判断留给你。

---

## 5.2 接口契约：ToolBase 增加 3 个类属性

在 `_base.py` 的类属性区追加（其余不变）：

```python
requires_confirmation: ClassVar[bool | None] = None
max_retries: ClassVar[int] = 0
retry_backoff: ClassVar[float] = 0.1
```

> 完整 `_base.py` 即在 T4 版本基础上加这三行；`call`/`__call__`/schema 逻辑不变。

---

## 5.3 接口契约：新建 `_governance.py`

### 组件说明

| 组件 | 种类 | 职责 |
|---|---|---|
| `ToolHook` | 基类 | before / after，默认空实现，子类按需覆盖 |
| `Approver` | ABC | `approve(tool, kwargs) -> bool` |
| `AutoApprover` | Approver | 固定允许/拒绝 |
| `ConsoleApprover` | Approver | CLI 交互审批 |
| `needs_confirmation` | 函数 | 三态规则 → bool |
| `run_governed` | 函数 | 编排 before → 确认 → 重试 → after |

### 完整代码

```python
import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ._response import ToolResponse, ToolStatus

if TYPE_CHECKING:
    from ._base import ToolBase
    from ._toolkit import Toolkit


class ToolHook:
    """前后 Hook 基类：子类只覆盖关心的方法。"""

    def before(
        self, tool: "ToolBase", kwargs: dict[str, Any]
    ) -> dict[str, Any] | None:
        return None

    def after(
        self,
        tool: "ToolBase",
        kwargs: dict[str, Any],
        response: ToolResponse,
    ) -> None:
        return None


class Approver(ABC):
    """审批回调：True 放行，False 拒绝。"""

    @abstractmethod
    async def approve(
        self, tool: "ToolBase", kwargs: dict[str, Any]
    ) -> bool: ...


class AutoApprover(Approver):
    """自动审批（测试/批处理）。"""

    def __init__(self, allow: bool) -> None:
        self.allow = allow

    async def approve(
        self, tool: "ToolBase", kwargs: dict[str, Any]
    ) -> bool:
        return self.allow


class ConsoleApprover(Approver):
    """CLI 人工审批：打印工具名+参数，读 y/n。"""

    async def approve(
        self, tool: "ToolBase", kwargs: dict[str, Any]
    ) -> bool:
        print(f"[审批] 工具 {tool.name!r} 请求执行，参数：{kwargs}")
        answer = await asyncio.to_thread(input, "允许执行？(y/n): ")
        return answer.strip().lower() in ("y", "yes")


def needs_confirmation(
    tool: "ToolBase", kwargs: dict[str, Any]
) -> bool:
    """三态规则：None→非只读需确认；True→总是；False→从不。"""
    flag = tool.requires_confirmation
    if flag is None:
        return not tool.is_read_only
    return flag


def _backoff(attempt: int, base: float) -> float:
    return base * (2**attempt)


async def run_governed(
    toolkit: "Toolkit",
    tool: "ToolBase",
    kwargs: dict[str, Any],
) -> ToolResponse:
    """在 Toolkit 门面编排：before → 确认 → 重试 → after。"""
    # 1) before hooks（可改 kwargs）
    for hook in toolkit.hooks:
        updated = hook.before(tool, kwargs)
        if updated is not None:
            kwargs = updated

    # 2) 人工确认
    if needs_confirmation(tool, kwargs):
        approver = toolkit.approver
        if approver is None:
            # 配置错误：需要授权却没有审批者，绝不默认放行
            raise RuntimeError(
                f"工具 {tool.name!r} 需要人工确认，但 Toolkit 未配置 approver"
            )
        if not await approver.approve(tool, kwargs):
            response = ToolResponse.denied(f"用户拒绝执行 {tool.name}")
            for hook in toolkit.hooks:
                hook.after(tool, kwargs, response)
            return response

    # 3) 执行 + 重试（只重试 ERROR）
    attempt = 0
    while True:
        response = await tool(**kwargs)
        if (
            response.status is not ToolStatus.ERROR
            or attempt >= tool.max_retries
        ):
            break
        await asyncio.sleep(_backoff(attempt, tool.retry_backoff))
        attempt += 1

    # 4) after hooks
    for hook in toolkit.hooks:
        hook.after(tool, kwargs, response)
    return response
```

> 解耦：`_governance.py` 不运行时 import `_toolkit`（仅 TYPE_CHECKING），
> 通过 `toolkit.hooks` / `toolkit.approver` 鸭子访问，避免循环导入。

---

## 5.4 接口契约：改造 `_toolkit.py`

相对 T4 的变化：

1. `__init__` 增加 `hooks=None, approver=None`，保存为 `self.hooks / self.approver`；
2. 增加 `add_hook(hook)`、`set_approver(approver)`；
3. `call_tool` 改为调 `run_governed`。

在 `_toolkit.py` 顶部追加导入：

```python
from ._governance import Approver, ToolHook, run_governed
```

`__init__` 签名与调用收口改为：

```python
def __init__(
    self,
    tools: list[ToolBase] | None = None,
    *,
    hooks: list[ToolHook] | None = None,
    approver: Approver | None = None,
) -> None:
    self._tools: OrderedDict[str, RegisteredTool] = OrderedDict()
    for tool in tools or []:
        self.register_tool(tool)
    self.hooks: list[ToolHook] = list(hooks or [])
    self.approver: Approver | None = approver

def add_hook(self, hook: ToolHook) -> None:
    self.hooks.append(hook)

def set_approver(self, approver: Approver) -> None:
    self.approver = approver

async def call_tool(self, name: str, **kwargs: Any) -> ToolResponse:
    """门面调用：经 Hook/确认/重试治理，再由 tool(**kwargs) 走 T2 执行收口。"""
    tool = self.get_tool(name)
    return await run_governed(self, tool, kwargs)
```

> 其余注册/注销/查找/分组/schema 方法保持 T4 不变。

---

## 5.5 留给你的动手任务

### 1) 给 `_base.py` 加 3 个类属性；新建 `_governance.py`；改造 `_toolkit.py`。

### 2) 写演示 `examples/tool_t5_governance.py`：

```python
import asyncio
from typing import ClassVar

from hello_agents.tool._base import ToolBase
from hello_agents.tool._governance import (
    AutoApprover,
    ConsoleApprover,
    ToolHook,
)
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit


class EchoTool(ToolBase):
    """只读：应免确认。"""

    name = "echo"
    description = "回显"
    is_read_only = True
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text)


class WriteNoteTool(ToolBase):
    """写操作：默认需确认（这里用 ConsoleApprover 真人交互）。"""

    name = "write_note"
    description = "写入一条笔记"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"content": {"type": "string"}},
        "required": ["content"],
    }

    async def call(self, *, content: str) -> ToolResponse:
        return ToolResponse.succeed(f"已写入：{content}")


class FlakyTool(ToolBase):
    """前两次失败、第三次成功：演示重试。"""

    name = "flaky"
    description = "不稳定工具"
    max_retries: ClassVar[int] = 2
    retry_backoff: ClassVar[float] = 0.05
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    attempts: ClassVar[int] = 0

    async def call(self) -> ToolResponse:
        type(self).attempts += 1
        if type(self).attempts < 3:
            return ToolResponse.fail("transient error")
        return ToolResponse.succeed("ok")


class LoggingHook(ToolHook):
    def before(self, tool, kwargs):
        print(f"[log] before {tool.name} kwargs={kwargs}")
        return None

    def after(self, tool, kwargs, response):
        print(f"[log] after {tool.name} status={response.status}")


async def main() -> None:
    toolkit = Toolkit(tools=[EchoTool(), WriteNoteTool(), FlakyTool()])
    toolkit.add_hook(LoggingHook())

    # 只读免确认（approver 配不配对它无所谓）
    print((await toolkit.call_tool("echo", text="hi")).get_text())

    # 写操作：交互审批；想自动跑可换成 AutoApprover(True)
    toolkit.set_approver(ConsoleApprover())
    print((await toolkit.call_tool("write_note", content="hello")).get_text())

    # 重试：最终成功
    FlakyTool.attempts = 0
    print((await toolkit.call_tool("flaky")).get_text())


asyncio.run(main())
```

> 想离线不交互地跑演示，可把 `ConsoleApprover()` 换成 `AutoApprover(True)`。

### 3) 写测试 `tests/test_tool_t5.py`（离线、确定性，用 AutoApprover）：

**Hook**
- 记录型 Hook 的 before/after 都被调用、after 能拿到最终 response；
- before 返回新 dict 时 kwargs 被覆盖（断言工具收到改后的值）；
- 多个 Hook 的 before 按注册顺序执行。

**确认**
- 只读工具：`AutoApprover` 不被调用也能成功（可在 approver 里放"被调用即失败"来证明免确认）；
- 写工具 + AutoApprover(True) → SUCCESS；
- 写工具 + AutoApprover(False) → DENIED、文本含"拒绝"；
- `requires_confirmation=True` 的只读工具仍会问；
- `requires_confirmation=False` 的写工具不问；
- 写工具、approver=None → RuntimeError。

**重试**（用计数器工具，backoff 设 0 或 monkeypatch `asyncio.sleep`）
- 前 2 次 ERROR、第 3 次成功，max_retries=2 → SUCCESS、总调用 3 次；
- max_retries=0（默认）→ ERROR、只调用 1 次；
- max_retries=1 且一直 ERROR → 调用 2 次、最终 ERROR；
- DENIED 结果不触发重试；
- 用 monkeypatch 替换 `hello_agents.tool._governance.asyncio.sleep` 避免真实等待。

**协同**
- 一次 call_tool 中：LoggingHook 记录 + AutoApprover 放行 + flaky 重试，最终 SUCCESS，
  且 after 只执行一次。

---

## 5.6 验收清单

- [ ] `_base.py` 含 3 个新类属性；
- [ ] `_governance.py` 与契约一致（Hook/Approver/needs_confirmation/run_governed）；
- [ ] `_toolkit.py` 持有 hooks/approver、call_tool 走 run_governed；
- [ ] tool_t5 演示三类能力正确；
- [ ] test_tool_t5 全绿；
- [ ] T1–T4 回归全绿；
- [ ] ruff 干净：

```powershell
uv run ruff check hello_agents/tool examples/tool_t5_governance.py tests/test_tool_t5.py
uv run ruff format --check hello_agents/tool examples/tool_t5_governance.py tests/test_tool_t5.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py -q
```

---

## 5.7 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| ToolHook.before/after | `_base.ToolMiddlewareBase.on_tool_call` | 两段式替代洋葱；不做流式 next_handler |
| Approver + needs_confirmation | permission 引擎 PermissionDecision(ALLOW/ASK/DENY) | 简化为单个审批回调；不做规则/沙箱 |
| ToolResponse.denied | ASK 被拒 / DENIED | 语义对应 |
| 工具层 max_retries/backoff | （agentscope 工具层未统一做重试） | 我们显式补、默认关 |
| run_governed 编排 | ToolBase.__call__ 洋葱 + toolkit 执行块 | 我们上移到 Toolkit 门面 |

---

## 完成后

把 `_governance.py`、更新后的 `_base.py`/`_toolkit.py`、tool_t5 输出与 pytest 结果
贴给我 review。通过后进入 **T6：内置文件/搜索工具（read_file / write_file / glob / grep）**。
