"""examples/ 里的示例脚本能跟上模型层的签名变更。

`examples/` 不在 pytest 的收集范围里，一个漏改的 `_call_api` 覆盖签名只会在用户真跑
脚本时才炸——M6 给 `_call_api` 加了 `tools` / `tool_choice` 两个关键字参数，
`examples/model_m5_demo.py` 里的子类就漏了，而全绿的测试套件完全看不见。

这里只跑**不产生费用**的部分：m5 demo 的第 3 部分把 base_url 指向 127.0.0.1 的
discard 端口，不会发出真实请求。
"""

import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.asyncio
async def test_m5_demo_offline_part_survives_call_api_signature_change():
    """m5 demo 的 `_Counting` 覆盖了 `_call_api`，必须吃得下基类传的新关键字。

    修复前这里会以 `TypeError: _Counting._call_api() got an unexpected keyword
    argument 'tools'` 失败。
    """
    sys.path.insert(0, str(EXAMPLES))
    import model_m5_demo

    await model_m5_demo.part3_retry_exhausted("deepseek:deepseek-flash")
