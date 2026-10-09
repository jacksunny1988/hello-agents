# 里程碑 T11：端到端整合 —— 最小 Agent 主循环（决策 → 执行 → 回灌 → 多轮）

> 目标：把已完成的 model 子系统、tool 子系统、bridge 桥接层组装成一个能跑多轮工具调用的
> 最小 Agent。主循环只写在 examples（约束 13：工具框架不内置主循环）。
> 依赖（均已读 / 已实现）：
> `model`
> 的 build_model / ChatResponse，
> `tool`
> 的 Toolkit，
> `bridge`
> 。



***

## 11.1 设计原理（先理解）

### 1) Agent 主循环到底在循环什么

一个 ReAct 风格工具型 Agent 的每一轮：



```
① 把「历史消息 + 可用工具 schema」发给模型
② 模型返回：一段正文，和/或，若干 tool_calls
③ 把这轮 assistant 消息（含正文与调用）追加进历史
④ 若有 tool_calls：执行它们，把每个结果以 role=tool 回灌进历史，回到 ①
   若没有 tool_calls：模型给的就是最终答案，结束
```

循环的本质：**模型用 tool\_calls 表示 "我还需要信息 / 动作"，用纯文本表示 "我答完了"**。

### 2) 为什么 assistant 消息要先入历史、再执行工具

协议要求 role=tool 的结果必须紧跟在含 tool\_calls 的 assistant 消息之后。模型下一轮要同时看到

"我上一轮决定调什么"（assistant）和 "调的结果"（tool），否则配对失败、上下文断裂。

顺序固定为：`assistant(tool_calls)` → `tool(result)` × N。

### 3) 并行工具调用

模型一次可能返回多个**互相独立**的 tool\_calls。用 `asyncio.gather` 并发执行、按原顺序回灌

（与 model 层 execute\_tool\_calls 的并发语义一致），总耗时取最慢的一个，而不是逐个累加。

### 4) 为什么还需要一个 "喂 schema" 的适配壳（关键衔接点）



* model 的 `__call__(tools=)` 只接受 **model 自己的 Tool 实例**（内部 `to_openai_tools`

  调 `tool.function_spec()`）；

* tool 子系统里是 `ToolBase`，两边类型不同，且 tool 包不能 import model；

* 因此 bridge 新增 `ToolkitModelTool`：实现 model 的 Tool 接口、字段从 ToolBase 拷贝，

  **唯一用途是让 model 能导出 function schema 给模型看**。

那执行为什么不直接用它的 `run` /model 自带的 `execute_tool_calls`？因为 model M6 那套

闭环是简化版，**没有 tool 子系统的审批、重试、超时、Hook、MCP 适配**。所以：



* **给模型看 schema**：ToolkitModelTool（描述壳）；

* **真正执行**：bridge.run\_tool\_call → Toolkit（保留全部治理与状态）。

`ToolkitModelTool.run` 仍转发到对应工具，纯粹是防止被误用时不报错；主循环不经过它。

### 5) 停止条件与兜底



* 正常停止：响应里没有 tool\_calls；

* **max\_rounds 兜底**：模型可能反复调工具（或陷入循环），必须有硬上限，到点抛错而不是无限跑；

* finished\_reason 为 `length`（撞长度截断）属异常情况，本里程碑不自动修复，打印提示即可；

* 空工具集时传 `tools=None`（空数组在部分端点是另一种语义、可能被拒）。

### 6) 中断与资源清理的边界



* CancelledError 不在循环内吞（T2/T5 一贯结论）；

* 但 MCP 连接等资源必须在**最外层** finally 关闭。主循环本身不负责创建 / 关闭 MCP，

  由调用方用 `try/finally` 或 `async with MCPClient` 管理。



***

## 11.2 bridge 新增：`bridge/_model_adapter.py`



```
from collections.abc import Sequence

from hello_agents.model._tool import Tool as ModelTool
from hello_agents.tool._base import ToolBase
from hello_agents.tool._toolkit import Toolkit


class ToolkitModelTool(ModelTool):
    """把 tool 子系统的 ToolBase 包装成 model 层 Tool。

    仅用于 function_spec() 向模型导出 schema；执行走 bridge.run_tool_call，
    以保留审批/重试/超时/Hook/MCP 等治理。
    """

    def __init__(self, tool: ToolBase) -> None:
        self.name = tool.name
        self.description = tool.description
        self.parameters = tool.input_schema
        self._tool = tool

    async def run(self, arguments: dict) -> str:
        # 正常主循环不经过这里（执行用 run_tool_call）；保留转发避免误用。
        response = await self._tool(**arguments)
        return response.get_text()


def model_tools_for(toolkit: Toolkit) -> Sequence[ToolkitModelTool]:
    """导出 Toolkit 中全部工具的 model 层描述壳。"""
    return [ToolkitModelTool(tool) for tool in toolkit.list_tools()]
```

更新 `bridge/__init__.py` 增加导出：



```
from ._model_adapter import ToolkitModelTool, model_tools_for
from ._tool_bridge import (
    parse_tool_arguments,
    run_tool_call,
    tool_result_message,
)

__all__ = [
    "ToolkitModelTool",
    "model_tools_for",
    "parse_tool_arguments",
    "run_tool_call",
    "tool_result_message",
]
```



***

## 11.3 主循环：`examples/agent_t11.py`



```
"""T11：最小 Agent 主循环（examples 演示，框架不内置）。"""

import asyncio

from hello_agents.bridge import model_tools_for, run_tool_call
from hello_agents.model.message import TextBlock
from hello_agents.model._response import ChatResponse


async def run_agent(
    model,
    toolkit,
    messages,
    *,
    max_rounds: int = 8,
    verbose: bool = True,
) -> ChatResponse:
    """跑工具型 Agent，直到模型不再调工具；返回最后一轮响应。"""
    for round_no in range(1, max_rounds + 1):
        tools = list(model_tools_for(toolkit))

        response = await model(messages, tools=tools or None)
        messages.append(response.to_message())

        text = "".join(
            block.text
            for block in response.content
            if isinstance(block, TextBlock)
        )
        if text and verbose:
            print(f"[assistant #{round_no}] {text}")

        calls = response.get_tool_calls()
        if not calls:
            return response

        if verbose:
            for call in calls:
                print(f"[tool-call #{round_no}] {call.name} {call.arguments}")

        results = await asyncio.gather(
            *(run_tool_call(call, toolkit) for call in calls)
        )
        messages.extend(results)

    raise RuntimeError(
        f"超过最大轮次 {max_rounds}，模型仍在调用工具，已停止"
    )
```

### 可运行演示入口（同文件追加）



```
from hello_agents.model.providers._openai_compat import build_model
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit


def _build_toolkit():
    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def add(a: float, b: float) -> float:
        """两数相加。

        Args:
            a: 第一个数
            b: 第二个数
        """
        return a + b

    toolkit.register_function(add)
    return toolkit


async def main() -> None:
    from hello_agents.model.message import Message

    model = build_model("dashscope:qwen3.7-plus")
    toolkit = _build_toolkit()

    messages = [
        Message.user("请帮我算一下 123 加 456 等于多少，调用 add 工具。")
    ]
    final = await run_agent(model, toolkit, messages)
    print("最终答案：", final.get_text() if hasattr(final, "get_text") else final)


if __name__ == "__main__":
    asyncio.run(main())
```

> `ChatResponse`
> 没有 get_text ()，演示里可从 content 的 TextBlock 取；保留上面写法时
> 请用
> `"".join(b.text for b in final.content if isinstance(b, TextBlock))`
> 。
> 换 provider 改 spec 即可：
> `deepseek:deepseek-flash`
> 、
> `zhipu:glm-5.2`
> 。



***

## 11.4 留给你的动手任务

### 1) 新增 `bridge/_model_adapter.py` 并更新 `bridge/__init__.py`。

### 2) 写 `examples/agent_t11.py`（主循环 + 演示入口）。

### 3) 离线测试主循环 `tests/test_tool_t11.py`（用 "脚本化假模型"，不发请求）：



```
"""T11：主循环离线测试——脚本化假模型驱动，验证消息顺序/停止/并行/轮次上限"""

import pytest

from hello_agents.bridge import model_tools_for
from hello_agents.model._response import ChatResponse, FinishedReason
from hello_agents.model.message import TextBlock, ToolCallBlock
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit

# 直接复用 examples 里的主循环（沿用项目导入 examples 的既有方式）
from examples.agent_t11 import run_agent


def _toolkit():
    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def echo(message: str) -> str:
        """回显。

        Args:
            message: 文本
        """
        return f"echo:{message}"

    toolkit.register_function(echo)
    return toolkit


class ScriptedModel:
    """按预设顺序返回 ChatResponse；记录每次收到的消息数。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.observed = []

    async def __call__(self, messages, tools=None):
        self.observed.append((len(messages), tools))
        return self._responses.pop(0)


def _text_response(text):
    return ChatResponse(
        content=[TextBlock(text=text)],
        finished_reason=FinishedReason.COMPLETED,
    )


def _call_response(name, arguments, call_id, finish=FinishedReason.TOOL_CALLS):
    return ChatResponse(
        content=[ToolCallBlock(name=name, arguments=arguments, id=call_id)],
        finished_reason=finish,
    )


async def test_direct_answer_stops_in_one_round():
    model = ScriptedModel([_text_response("你好，这是答案")])
    final = await run_agent(model, _toolkit(), [], verbose=False)
    assert "答案" in final.content[0].text
    assert len(model.observed) == 1


async def test_tool_then_answer_has_correct_message_order():
    messages = []
    model = ScriptedModel(
        [
            _call_response("echo", '{"message":"hi"}', "c1"),
            _text_response("完成"),
        ]
    )
    await run_agent(model, _toolkit(), messages, verbose=False)

    # 历史顺序：assistant(tool_call) → tool(result) → assistant(final)
    roles = [m.role.value for m in messages]
    assert roles == ["assistant", "tool", "assistant"]
    assert messages[1].content[0].tool_call_id == "c1"
    assert messages[1].content[0].output == "echo:hi"


async def test_two_parallel_calls_both_execute():
    messages = []
    model = ScriptedModel(
        [
            ChatResponse(
                content=[
                    ToolCallBlock(
                        name="echo",
                        arguments='{"message":"a"}',
                        id="c1",
                    ),
                    ToolCallBlock(
                        name="echo",
                        arguments='{"message":"b"}',
                        id="c2",
                    ),
                ],
                finished_reason=FinishedReason.TOOL_CALLS,
            ),
            _text_response("done"),
        ]
    )
    await run_agent(model, _toolkit(), messages, verbose=False)

    tool_msgs = [m for m in messages if m.role.value == "tool"]
    assert len(tool_msgs) == 2
    outputs = {m.content[0].output for m in tool_msgs}
    assert outputs == {"echo:a", "echo:b"}


async def test_max_rounds_raises():
    looping = ScriptedModel(
        [
            _call_response("echo", '{"message":"x"}', f"c{i}")
            for i in range(10)
        ]
    )
    with pytest.raises(RuntimeError, match="最大轮次"):
        await run_agent(looping, _toolkit(), [], max_rounds=3, verbose=False)


def test_model_tools_for_wraps_registered_tools():
    shells = list(model_tools_for(_toolkit()))
    assert len(shells) == 1
    spec = shells[0].function_spec()
    assert spec["function"]["name"] == "echo"
```

> 若项目导入 examples 用的是别的约定（model 阶段 test_model_examples 的方式），按同样
> 方式 import；必要时在 tests/conftest.py 把 examples 加入 sys.path。

### 4) 真实端到端（marker=e2e，默认 skip，发真实请求、产生少量费用）：

`tests/test_tool_t11_e2e.py`：



```
"""T11 真实 e2e：模型经主循环调用 add 工具作答。默认 skip，RUN_MODEL_E2E=1 打开。"""

import os

import pytest

from hello_agents.model.message import Message
from hello_agents.model.providers._openai_compat import build_model
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit

pytestmark = pytest.mark.e2e


async def test_agent_uses_add_tool():
    if not os.getenv("RUN_MODEL_E2E"):
        pytest.skip("set RUN_MODEL_E2E=1 to run")

    toolkit = Toolkit(tools=[], approver=AutoApprover(True))

    def add(a: float, b: float) -> float:
        """两数相加。

        Args:
            a: 第一个数
            b: 第二个数
        """
        return a + b

    toolkit.register_function(add)

    model = build_model("dashscope:qwen3.7-plus")
    from examples.agent_t11 import run_agent

    messages = [Message.user("用 add 算 123+456，直接给结果。")]
    final = await run_agent(model, toolkit, messages, verbose=False)
    text = "".join(b.text for b in final.content if hasattr(b, "text"))
    assert "579" in text
```

运行：



```
$env:RUN_MODEL_E2E="1"; uv run pytest -m e2e tests/test_tool_t11_e2e.py -q
```



***

## 11.5 验收清单



* [x] `_model_adapter.py`、`agent_t11.py` 与契约一致；

* [x] test\_tool\_t11 离线全绿（单轮直答 / 工具后作答消息顺序 / 并行 / 轮次上限 /schema 壳）；

* [x] T1–T10 回归全绿；

* [x] ruff 干净；

* [x] （推荐，少量费用）e2e 通过：模型真的调用 add 并给出 579：



```
uv run ruff check hello_agents/bridge examples/agent_t11.py tests/test_tool_t11.py
uv run ruff format --check hello_agents/bridge examples/agent_t11.py tests/test_tool_t11.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py tests/test_tool_t4.py tests/test_tool_t5.py tests/test_tool_t6.py tests/test_tool_t7.py tests/test_tool_t8.py tests/test_tool_t9.py tests/test_tool_t10.py tests/test_tool_t11.py -q
```



***

## 11.6 全旅程回顾：你现在拥有一个怎样的系统



```
examples/agent_t11.py        主循环（决策→执行→回灌→多轮），只在 examples
        │
   ┌────┴───────────────────────────────┐
hello_agents/bridge/                    唯一认识两边的薄适配
   ├─ _tool_bridge.py   执行编排 + 状态有损映射 + 锚点保真
   └─ _model_adapter.py 仅向模型导出 schema 的描述壳
        │
   ┌────┴───────────────┐   ┌────────────────────────────┐
hello_agents/model/         hello_agents/tool/
  统一抽象/多provider/       ToolBase/执行收口/函数适配/
  流式/重试取消/结构化/      Toolkit分组/审批·Hook·重试/
  YAML卡片                  内置 fs·search·bash / MCP stdio·http
```

贯穿始终、可迁移到任何 Agent 框架的核心认知：



1. **统一中间模型 + 薄适配**：协议差异收敛到 Formatter/Adapter 一层，业务只依赖中间模型；

2. **横切逻辑在边界收口**：重试 / 取消 / 超时 / 审批集中在基类或门面，工具 / 业务保持纯净；

3. **生命周期必须有人负责**：流式聚合、子进程与连接的关闭，漏了就是孤儿 / 僵尸 / 泄漏；

4. **跨轮锚点保真**：tool\_call\_id 等标识符只有透传义务、没有生成权；

5. **状态跨边界是有损映射**：denied/interrupted 进入布尔 is\_error 时要显式、可追溯。

> 毕业任务（呼应 model 的 M9）：脱稿在
> `docs/tool/MY-TOOL-DESIGN.md`
> 里，不看代码，
> 用自己的话画出 tool 子系统的分层、每个模块职责、三条最容易出 bug 的链路及你的解法；
> 再把 model 与 tool 两篇合并成一份属于你自己的《Agent 设计手册》。能讲清楚，才是真的吃透。



***

## 完成后

把 bridge 改动、agent\_t11、离线 pytest 与（若跑了）e2e 输出贴给我做最终 review。