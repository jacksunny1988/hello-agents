"""T11 真实 e2e：模型经主循环调用 add 工具作答。默认 skip，RUN_MODEL_E2E=1 打开。"""

import os
import sys
from pathlib import Path

import pytest

from hello_agents.model.message import Message
from hello_agents.model.providers._openai_compat import build_model
from hello_agents.tool._governance import AutoApprover
from hello_agents.tool._toolkit import Toolkit

# examples/ 不在 pytest 收集范围、也无 __init__.py；沿用 test_model_examples.py 的约定
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples"))
from agent_t11 import run_agent

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

    messages = [Message.user("用 add 算 123+456，直接给结果。")]
    final = await run_agent(model, toolkit, messages, verbose=False)
    text = "".join(b.text for b in final.content if hasattr(b, "text"))
    assert "579" in text
