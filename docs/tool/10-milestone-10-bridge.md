# 里程碑 T10：薄桥接层——ToolResponse ↔ model 层 Message/ToolResultBlock

> 目标：在不破坏两个子系统独立性的前提下，写唯一"同时认识 model 和 tool"的薄适配层，
> 把模型的一次 tool_call 变成"Toolkit 执行 + 可回灌的 model Message"。
> 对齐（已读）：`hello_agents/model/message.py`、`model/_formatter.py`。

---

## 10.1 设计原理（先理解）

### 1) 为什么桥接层要单独存在、且是"薄"的

- model 与 tool 是两个可独立复用的子系统，任何一方都不该 import 另一方（约束 8）；
- 但端到端 Agent 必须把两边接起来。把"认识两边协议"的代码集中到一个桥接层，
  其余代码仍只依赖自己的子系统；
- "薄"指它**不含业务逻辑、不做 Agent 主循环**，只做两件事：
  - **model → tool**：把模型的 ToolCallBlock（参数是 JSON 字符串）解析成 kwargs，交给 Toolkit；
  - **tool → model**：把 ToolResponse 归一为可回灌的 `role=tool` Message。

### 2) 两个方向的数据形态

**model → tool（解析调用）**

- `ToolCallBlock.arguments` 是 **JSON 字符串**（对齐 OpenAI 协议 `function.arguments`），
  需 `json.loads` 成 dict 作为 kwargs；
- 工具名直接用 `call.name`——模型看到的工具名（get_function_schema 导出的 name，
  含 MCP 的 `mcp__server__tool`）与 Toolkit 注册名一致，无需再映射。

**tool → model（结果回灌）**

- formatter 要求：`role=tool` 的消息**只能包含 ToolResultBlock**（不能混 TextBlock），
  每个结果展开为 `{role:"tool", tool_call_id, content, name?}`；
- 因此桥接产物固定为：

```python
Message(role=Role.TOOL, name=call.name,
        content=[ToolResultBlock(
            tool_call_id=call.id, output=resp.get_text(),
            is_error=..., name=call.name)])
```

- `tool_call_id` 必须原样取 `call.id`——这正是 model 阶段强调的跨轮回指锚点。

### 3) 状态映射：ToolStatus → is_error（有损映射，必须想清楚）

model 的 ToolResultBlock 只有布尔 `is_error`，没有 denied/interrupted 态：

| ToolStatus | is_error | 给模型的含义 |
|---|---|---|
| success | False | 正常结果 |
| error | True | 工具报错，output 为错误文本 |
| denied | True | 用户拒绝执行，output 说明被拒，模型应改道 |
| interrupted | True | 被中断，模型可决定是否重来 |

即**只有 success 映射为 False，其余一律 True**。denied/interrupted 的区分在 tool 层保留，
进入 model 协议时被"压扁"成错误，靠 output 文本传达原因。这是两个状态模型之间的**有损映射**，
要在代码注释里写明，避免后人误以为信息没丢。

### 4) 异常收口的边界（哪些兜底、哪些传播）

桥接的便利函数在主循环里被反复调用，需对"模型侧常见问题"做兜底，对"配置错误"保持 fail loud：

- 参数不是合法 JSON、或 JSON 不是对象 → **不执行**，直接回灌 is_error 结果；
- 工具名未注册（KeyError）→ 回灌 is_error，告诉模型没有该工具；
- 审批未配置（RuntimeError）→ **传播**，这是部署配置问题，不能伪装成工具结果；
- CancelledError → **传播**（不抓 BaseException）。

---

## 10.2 目录与文件

新建顶层子包 `hello_agents/bridge/`（与 model、tool 平级，是唯一 import 两边的地方）：

```
hello_agents/bridge/
  __init__.py
  _tool_bridge.py
```

---

## 10.3 接口契约：`bridge/_tool_bridge.py`

```python
import json
from typing import Any

from hello_agents.model.message import (
    Message,
    Role,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit


def parse_tool_arguments(call: ToolCallBlock) -> dict[str, Any] | None:
    """把模型给的 JSON 字符串参数解析成 dict；非法或非对象返回 None。"""
    try:
        data = json.loads(call.arguments)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def tool_result_message(
    call: ToolCallBlock, resp: ToolResponse
) -> Message:
    """把一次工具执行结果转成可回灌的 role=tool Message。

    状态有损映射：仅 success -> is_error=False；error/denied/interrupted
    均压扁为 is_error=True，原因靠 output 文本传达。
    """
    return Message(
        role=Role.TOOL,
        name=call.name,
        content=[
            ToolResultBlock(
                tool_call_id=call.id,
                output=resp.get_text(),
                is_error=resp.status is not ToolStatus.SUCCESS,
                name=call.name,
            )
        ],
    )


async def run_tool_call(
    call: ToolCallBlock, toolkit: Toolkit
) -> Message:
    """执行模型的一次 tool_call，返回可直接追加进对话历史的 Message。

    - 参数非法 / 工具未注册：回灌 is_error，不抛异常；
    - 审批未配置等 RuntimeError、CancelledError：照常向上传播。
    """
    kwargs = parse_tool_arguments(call)
    if kwargs is None:
        resp = ToolResponse.fail(
            f"工具参数不是合法 JSON 对象：{call.arguments!r}"
        )
        return tool_result_message(call, resp)

    try:
        result = await toolkit.call_tool(call.name, **kwargs)
    except KeyError:
        result = ToolResponse.fail(f"未注册的工具：{call.name}")
    return tool_result_message(call, result)
```

### `bridge/__init__.py`

```python
from ._tool_bridge import (
    parse_tool_arguments,
    run_tool_call,
    tool_result_message,
)

__all__ = [
    "parse_tool_arguments",
    "run_tool_call",
    "tool_result_message",
]
```

---

## 10.4 留给你的动手任务

### 1) 创建 `bridge/` 两个文件。

### 2) 写测试 `tests/test_tool_t10.py`（离线，无需模型/网络）：

```python
"""T10：薄桥接层——参数解析 · 状态映射 · 执行回灌，全部离线确定性"""

import pytest

from hello_agents.bridge import (
    parse_tool_arguments,
    run_tool_call,
    tool_result_message,
)
from hello_agents.model.message import (
    Message,
    Role,
    ToolCallBlock,
    ToolResultBlock,
)
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit


def _call(name="echo", arguments='{"message":"hi"}', call_id="call-1"):
    return ToolCallBlock(name=name, arguments=arguments, id=call_id)


def _toolkit(approver=None):
    toolkit = Toolkit(tools=[], approver=approver)

    def echo(message: str) -> str:
        """原样回显。

        Args:
            message: 要回显的文本
        """
        return f"echo:{message}"

    toolkit.register_function(echo)
    return toolkit


# --- parse_tool_arguments ---------------------------------------------------


def test_parse_arguments_decodes_object():
    assert parse_tool_arguments(_call()) == {"message": "hi"}


def test_parse_arguments_rejects_invalid_json():
    assert parse_tool_arguments(_call(arguments="{bad")) is None


@pytest.mark.parametrize("arguments", ["[1,2]", "123", '"str"', "null"])
def test_parse_arguments_rejects_non_object(arguments):
    assert parse_tool_arguments(_call(arguments=arguments)) is None


# --- tool_result_message：状态映射 ------------------------------------------


def test_success_maps_to_not_error():
    msg = tool_result_message(_call(), ToolResponse.succeed("ok"))
    block = msg.content[0]
    assert isinstance(block, ToolResultBlock)
    assert msg.role is Role.TOOL
    assert block.tool_call_id == "call-1"
    assert block.output == "ok"
    assert block.is_error is False
    assert block.name == "echo"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ToolResponse.fail("x"),
        lambda: ToolResponse.denied("x"),
    ],
)
def test_error_and_denied_map_to_error(factory):
    msg = tool_result_message(_call(), factory())
    assert msg.content[0].is_error is True


def test_interrupted_maps_to_error():
    resp = ToolResponse(status=ToolStatus.INTERRUPTED, content=[])
    msg = tool_result_message(_call(), resp)
    assert msg.content[0].is_error is True


# --- run_tool_call：端到端（不经真实模型）-----------------------------------


async def test_run_tool_call_executes_and_builds_message():
    msg = await run_tool_call(_call(), _toolkit(AutoApprover(True)))
    block = msg.content[0]
    assert block.is_error is False
    assert block.output == "echo:hi"
    assert block.tool_call_id == "call-1"  # 锚点保真


async def test_run_tool_call_bad_arguments_does_not_execute():
    msg = await run_tool_call(
        _call(arguments="oops"), _toolkit(AutoApprover(True))
    )
    assert msg.content[0].is_error is True
    assert "JSON" in msg.content[0].output


async def test_run_tool_call_unknown_tool():
    msg = await run_tool_call(
        _call(name="ghost"), _toolkit(AutoApprover(True))
    )
    assert msg.content[0].is_error is True
    assert "未注册" in msg.content[0].output


async def test_run_tool_call_denied():
    msg = await run_tool_call(_call(), _toolkit(AutoApprover(False)))
    assert msg.content[0].is_error is True


async def test_run_tool_call_missing_approver_raises():
    with pytest.raises(RuntimeError, match="approver"):
        await run_tool_call(_call(), _toolkit(None))
```

> 说明：`ToolResponse.denied` 是 T1 已有的 classmethod；INTERRUPTED 直接构造
> （ToolResponse 没有 interrupted classmethod）。

### 3) 写演示 `examples/tool_t10_bridge.py`（离线模拟，不连模型）：

- 构造 Toolkit（注册 echo，AutoApprover(True)）；
- 手工造一个 ToolCallBlock，`run_tool_call` 执行并打印回灌 Message；
- 再造一个坏参数 / 未注册工具的调用，观察 is_error 回灌。

### 4)（可选但推荐）验证桥接产物能被 formatter 消费：

在一个测试里 `to_openai_messages([msg])`，断言得到
`{"role":"tool","tool_call_id":"call-1","content":"echo:hi","name":"echo"}`，
确保桥接产物在真实出站链路上形状正确。

---

## 10.5 验收清单

- [x] `bridge/` 与契约一致（tool/model 子包内仍互不 import）；
- [x] test_tool_t10 全绿（含状态映射四态、坏参数、未注册、审批缺失传播）；
- [x] T1–T9 回归全绿；
- [x] ruff 干净：

```powershell
uv run ruff check hello_agents/bridge tests/test_tool_t10.py examples/tool_t10_bridge.py
uv run ruff format --check hello_agents/bridge tests/test_tool_t10.py examples/tool_t10_bridge.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py tests/test_tool_t6.py tests/test_tool_t7.py tests/test_tool_t8.py tests/test_tool_t9.py tests/test_tool_t10.py -q
```

> 可顺手确认 tool 包确实不依赖 model：

```powershell
uv run python -c "import hello_agents.tool; import hello_agents.model; print('independent ok')"
```

---

## 10.6 设计要点回顾

| 方向 | 桥接函数 | 关键约束 |
|---|---|---|
| model → tool | parse_tool_arguments | arguments 是 JSON 字符串；非对象/非法 → None |
| tool → model | tool_result_message | role=tool 仅含 ToolResultBlock；tool_call_id 保真 |
| 双向编排 | run_tool_call | 坏参/未注册回灌错误；配置错误/取消传播 |
| 状态映射 | —— | 仅 success→False，其余压扁为 True（有损） |

> 思考题：若未来 model 协议增加 denied 态（或要把 tool 的 metadata 一并回灌），
> 只需扩展 ToolResultBlock 与 tool_result_message，桥接层把"协议差异"集中在一处的价值就在这。

---

## 完成后

把 bridge 代码、pytest 结果与演示输出贴给我 review。
通过后进入 **T11：端到端整合——examples 里写最小 Agent 主循环（模型决策 → 桥接执行 → 回灌 → 多轮）**。
