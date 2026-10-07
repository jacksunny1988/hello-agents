# 里程碑 T2：执行收口（sync/async 归一 · 线程池 · 超时 · 异常结构化）

> 目标：让工具作者写的 `call` 无论是同步 `def` 还是 `async def`，都能被统一、安全地执行；
> per-tool 超时；业务异常转结构化结果、闭环不断；取消正确传播。
> 对标（只读）：`agentscope/tool/_toolkit.py`（280–388 行的单工具执行块）、
> `_adapters.py` 的 `FunctionTool.call`。

---

## 2.1 设计原理（先理解）

### 1) 为什么同步函数必须丢线程池

事件循环在**单一线程**里跑。若工具 `call` 是同步且阻塞（如 `time.sleep`、大计算、
同步 IO），直接调用会**卡住整个事件循环**——期间其它协程（流式、其它工具、心跳）全停。

- agentscope 的 `FunctionTool.call` 对同步函数是**直接 `self._func(**kwargs)`**（_adapters.py
  第 122 行），toolkit 里也是非协程就直接调用（_toolkit.py 第 314 行）。它默认同步工具
  很快，把阻塞风险留给了工具作者。
- 我们按你问题 5 的选择，统一用 **`asyncio.to_thread(call, **kwargs)`** 把同步 `call`
  丢到默认线程池执行，事件循环不被阻塞。这是我们相对 agentscope 的一处增强。

### 2) 为什么超时和异常要在框架层收口

Agent 闭环里，单个工具卡死或抛异常都不应让整个 Agent 崩掉：
- 超时 → 框架"不再等它"，返回 `ERROR` 结果，模型可据此改方案；
- 业务异常 → 捕获、转成 `ToolResponse.fail`（文本含异常类型与信息），模型能看到错误并自纠。

### 3) 取消（CancelledError）怎么处理——关键边界

这与你在 model M5 学的结论一致：**`CancelledError` 不能当普通异常吞掉**。

- Python 3.9+ `asyncio.CancelledError` 继承 `BaseException`（不是 `Exception`），
  所以 `except Exception` 不会误抓它；
- 我们的统一入口 **让 CancelledError 正常向上传播**，保证 `asyncio` 的取消/TaskGroup/
  gather 语义正确；
- `ToolStatus.INTERRUPTED`（T1 已预留）不在 `__call__` 里产生——它属于"**Agent 主循环
  想把取消当成一个结果**"的更外层边界（T11 examples 演示：主循环 `try/except
  CancelledError` 后构造 INTERRUPTED 响应）。本里程碑只保证取消能干净传播。

### 4) 一个必须知道的限制：线程里的同步函数停不下来

`asyncio.to_thread` 跑的同步函数，超时或取消时：
- 框架能做到"**不再等待**"（wait_for 抛 TimeoutError / awaitable 被取消）；
- 但 **Python 无法强杀线程**，线程内的阻塞调用（如同步网络请求、`time.sleep`）会继续
  跑到结束。这是 GIL/线程模型的固有限制。异步 `call` 则能随取消及时中断（取决于其内部
  是否正确响应取消）。

---

## 2.2 接口契约：新建 `_executor.py`

### 函数说明

| 函数 | 入参 | 返回 | 职责 |
|---|---|---|---|
| `_run_call` | tool, **kwargs | ToolResponse | 判断 call 同步/异步：协程则 await，否则 to_thread |
| `execute_tool` | tool, kwargs, timeout | ToolResponse | wait_for 包裹；超时→fail；业务异常→fail；取消传播 |

### 完整代码

```python
import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from ._response import ToolResponse

if TYPE_CHECKING:
    from ._base import ToolBase


async def _run_call(tool: "ToolBase", **kwargs: Any) -> ToolResponse:
    """把工具的 call（同步或异步）归一为一次 await，同步 call 丢线程池。"""
    call = tool.call
    if inspect.iscoroutinefunction(call):
        return await call(**kwargs)
    return await asyncio.to_thread(call, **kwargs)


async def execute_tool(
    tool: "ToolBase",
    kwargs: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> ToolResponse:
    """执行单个工具的统一收口。

    - timeout 为秒；None 表示不限（wait_for 接受 None）；
    - 超时      → ToolResponse.fail（status=ERROR）；
    - 业务异常  → ToolResponse.fail（文本含异常类型与信息）；
    - CancelledError 不捕获、直接向上传播。
    """
    kwargs = kwargs or {}
    try:
        return await asyncio.wait_for(
            _run_call(tool, **kwargs),
            timeout=timeout,
        )
    except TimeoutError:
        # asyncio.wait_for 超时抛内置 TimeoutError（3.11+ 与 asyncio.TimeoutError 同义）
        return ToolResponse.fail(f"工具 {tool.name!r} 在 {timeout} 秒后超时")
    except Exception as exc:
        # 仅兜底业务异常；CancelledError 继承 BaseException，不会落到这里
        return ToolResponse.fail(f"{type(exc).__name__}: {exc}")
```

> 解耦：`_executor.py` 只 import `_response`，不 import `_base`（tool 用 TYPE_CHECKING
> 仅作类型提示），避免循环导入。

---

## 2.3 接口契约：改造 `_base.py`

相对 T1 的三处变化：

1. 新增类属性 `timeout: ClassVar[float | None] = None`（per-tool 超时，秒）；
2. 抽象覆盖点 `call` 去掉 `async` 强制——**允许子类用 `def` 或 `async def` 覆盖**
   （`abstractmethod` 只要求"被实现"，不限制协程与否）；
3. `__call__` 改为调用 `execute_tool`，把执行收口交给框架。

### 完整新版 `_base.py`

```python
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ._executor import execute_tool
from ._response import ToolResponse


class ToolBase(ABC):
    """所有工具的统一抽象。工具作者：声明类属性 + 实现 `call`（def 或 async def）。"""

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    is_read_only: ClassVar[bool] = False
    is_concurrency_safe: ClassVar[bool] = True
    timeout: ClassVar[float | None] = None

    def __init__(self) -> None:
        self._validate_input_schema()

    def _validate_input_schema(self) -> None:
        """input_schema 必须是 type='object' 且含 properties；无参工具 properties 为空 dict。"""
        schema = self.input_schema
        if not (
            isinstance(schema, dict)
            and schema.get("type") == "object"
            and isinstance(schema.get("properties"), dict)
        ):
            raise ValueError(
                f"工具 {self.name!r} 的 input_schema 必须是 type='object' 且含 "
                f"properties 的 JSON Schema，收到：{schema!r}"
            )

    @abstractmethod
    def call(self, **kwargs: Any) -> ToolResponse:
        """业务逻辑。可写同步 def（框架自动丢线程池）或 async def。"""

    async def __call__(self, **kwargs: Any) -> ToolResponse:
        """框架统一入口：sync/async 归一、超时、异常兜底（见 _executor.execute_tool）。"""
        return await execute_tool(self, kwargs, timeout=self.timeout)

    def get_function_schema(self) -> dict[str, Any]:
        """产出 OpenAI function 形式的 schema（纯 dict，不依赖 model 层）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
```

> 兼容性：T1 的 `EchoTool` 用 `async def call`，在新版下依然合法、行为不变。

---

## 2.4 留给你的动手任务

### 1) 实现 `_executor.py`，并按 2.3 更新 `_base.py`。

### 2) 写演示 `examples/tool_t2_executor.py`，覆盖三种形态：

```python
import asyncio
import threading
from typing import ClassVar

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse, ToolStatus


class SyncAddTool(ToolBase):
    """同步 call：验证自动线程池（应在非主线程执行）。"""

    name = "sync_add"
    description = "同步加法（在线程池执行）"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    }

    def call(self, *, a: float, b: float) -> ToolResponse:
        return ToolResponse.succeed(str(a + b), thread=threading.get_ident())


class SlowTool(ToolBase):
    """异步慢工具：配合 timeout 验证超时。"""

    name = "slow"
    description = "睡眠若干秒"
    timeout: ClassVar[float | None] = 0.2
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"seconds": {"type": "number"}},
        "required": ["seconds"],
    }

    async def call(self, *, seconds: float) -> ToolResponse:
        await asyncio.sleep(seconds)
        return ToolResponse.succeed("done")


class BoomTool(ToolBase):
    """业务异常：验证异常被收口为 fail。"""

    name = "boom"
    description = "总是抛异常"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def call(self) -> ToolResponse:
        raise RuntimeError("something went wrong")


async def main() -> None:
    main_thread = threading.get_ident()

    add = await SyncAddTool()(a=1.0, b=2.0)
    print("sync:", add.status is ToolStatus.SUCCESS, add.get_text(),
          "off-thread:", add.metadata["thread"] != main_thread)

    slow = await SlowTool()(seconds=1)
    print("timeout:", slow.status is ToolStatus.ERROR, slow.get_text())

    no_timeout = SlowTool()
    no_timeout.timeout = None  # 实例级覆盖：不限时
    ok = await no_timeout(seconds=0.05)
    print("no-timeout:", ok.status is ToolStatus.SUCCESS)

    boom = await BoomTool()()
    print("error:", boom.status is ToolStatus.ERROR, boom.get_text())


asyncio.run(main())
```

期望打印：
- `sync: True 3.0 off-thread: True`
- `timeout: True 工具 'slow' 在 0.2 秒后超时`
- `no-timeout: True`
- `error: True RuntimeError: something went wrong`

### 3) 写测试 `tests/test_tool_t2.py`（离线、确定性）：

- **同步 call 正常**：SyncAddTool 返回 SUCCESS、结果正确；
- **同步 call 确实在别的线程**：metadata 里记录 `threading.get_ident()`，断言与主线程不同
  （这是 to_thread 生效的硬证据）；
- **异步 call 正常**：沿用 EchoTool 或新写；
- **超时 → ERROR**：SlowTool（timeout 短、sleep 长）返回 status=ERROR，文本含"超时"；
- **timeout=None 不限时**：sleep 很短时能 SUCCESS；
- **业务异常 → ERROR**：BoomTool 返回 status=ERROR，文本含异常类型名 `RuntimeError`；
- **取消传播（不被吞）**：

```python
import asyncio
import pytest

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse
from typing import ClassVar


class NeverReturns(ToolBase):
    name = "never"
    description = "永远等待"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    async def call(self) -> ToolResponse:
        await asyncio.Event().wait()  # 永不被 set
        return ToolResponse.succeed("unreachable")


async def test_cancellation_propagates():
    tool = NeverReturns()
    task = asyncio.create_task(tool())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

  要点：断言 CancelledError 抛出，证明框架没有吞掉取消（INTERRUPTED 归一留待 T11）。

### 4) 测试清单（实际交付：需求 → 测试对应表）

`tests/test_tool_t2.py` 共 13 条，与上面的需求逐条对应：

| 需求 | 测试函数 | 说明 |
|---|---|---|
| 同步 call 正常 | `test_sync_call_returns_success` | 断言 SUCCESS 与结果文本 |
| 同步 call 在别的线程 | `test_sync_call_runs_off_the_event_loop_thread` | to_thread 生效的硬证据 |
| 异步 call 正常 | `test_async_call_returns_success` | |
| （反向证据） | `test_async_call_stays_on_the_event_loop_thread` | 异步 call 不走线程池 |
| 超时 → ERROR | `test_timeout_returns_error` | 文本含"超时"与工具名 |
| （线程池分支） | `test_sync_call_is_also_subject_to_timeout` | 同步 call 同样受 wait_for 约束 |
| timeout=None 不限时 | `test_instance_level_timeout_none_means_unlimited` | sleep 故意超过类级 timeout |
| （默认值） | `test_default_timeout_is_none` | `ToolBase.timeout is None` |
| 业务异常 → ERROR | `test_sync_business_exception_becomes_error_response` | 文本 == `RuntimeError: something went wrong` |
| （异步分支） | `test_async_business_exception_becomes_error_response` | |
| （兜底本身） | `test_exception_does_not_escape_the_tool_boundary` | 调用方拿到结果对象而非异常 |
| 取消传播 | `test_cancellation_propagates_and_is_not_swallowed` | 含 `task.cancelled()` |
| （安全假设） | `test_cancelled_error_is_not_an_exception_subclass` | 改 `except BaseException` 立刻红 |

> 每条需求都做过变异验证（注入对应缺陷、确认测试变红、再还原），
> 例如：`wait_for` 的 timeout 失效 → 超时两条红；`self.timeout` 改成
> `type(self).timeout` → "不限时"那条红；`except Exception` 改成
> `except BaseException` → 取消那条红。

---

## 2.5 验收清单（T2 已完成）

- [x] `_executor.py` 与契约一致（同步 to_thread、异步 await、wait_for、两档 except）；
- [x] `_base.py` 已加 timeout、call 允许 sync/async、__call__ 走 execute_tool；
- [x] tool_t2_executor 四种情形输出符合预期（逐字一致）；
- [x] test_tool_t2 全绿（13 条，含"别的线程"与"取消传播"两条硬验证）；
- [x] T1 的 test_tool_t1 仍全绿（24 条回归）；
- [x] ruff 干净。

```powershell
uv run ruff check hello_agents/tool examples/tool_t2_executor.py tests/test_tool_t2.py
uv run ruff format --check hello_agents/tool examples/tool_t2_executor.py tests/test_tool_t2.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py -q
```

### lint 说明：`_executor.py` 的 `# noqa: BLE001`

实现与 2.2 的代码块有一处偏差——`except Exception as exc:` 那行加了 `# noqa: BLE001`：

```python
    except Exception as exc:  # noqa: BLE001
        # T2 契约：业务异常一律兜底转 fail；CancelledError 继承 BaseException，不会落到这里
        return ToolResponse.fail(f"{type(exc).__name__}: {exc}")
```

原因：本项目**没有** ruff 配置文件，而 ruff 0.16 的默认规则集包含 **413 条规则**
（实测 `ruff check --isolated` 仍会报），其中 BLE001（盲捕获 `Exception`）与本契约
直接冲突。收窄捕获类型会违背"业务异常一律兜底"，故就地 noqa 并保留理由注释。
根治方案是在 `pyproject.toml` 显式钉住 `[tool.ruff.lint] select`，**尚未做**。

---

## 2.6 agentscope 源码对照

| 你的实现 | agentscope | 对照/差异 |
|---|---|---|
| `_run_call`（iscoroutinefunction 判断） | `_toolkit.py:309-314`、`_adapters.py:119-122` | 判断方式相同；**同步分支我们用 to_thread，agentscope 直接调用** |
| `execute_tool` wait_for 超时 | bash 用 `asyncio.wait_for`（_backend.py:593）；MCP 用 read_timeout_seconds | 我们把超时统一到执行收口 |
| except Exception → fail | `_toolkit.py:352-368` | 语义对应（agentscope 转 ERROR chunk） |
| CancelledError 传播 | `_toolkit.py:370-384` 转 INTERRUPTED chunk | 我们在统一入口让其传播；INTERRUPTED 归一上移到 Agent 边界（T11） |

---

## 完成后

把 `_executor.py`、更新后的 `_base.py`、tool_t2 演示输出与 pytest 结果贴给我 review。
通过后进入 **T3：普通函数自动适配（类型注解 + docstring → JSON Schema）**。
