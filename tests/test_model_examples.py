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


def test_m7_examples_import_and_their_schemas_are_valid():
    """M7 两个脚本不在 pytest 收集范围内，至少保证「导入不炸 + schema 立得住」。

    不覆盖发请求的路径（那要 key 和费用）；`model_m7_probe.py` 用
    `strict_json_schema` 构造请求，所以这里顺带确认那条构造链是通的。
    """
    sys.path.insert(0, str(EXAMPLES))
    import model_m7_demo
    import model_m7_probe

    from hello_agents.model._structured import strict_json_schema

    schema = strict_json_schema(model_m7_demo.Person)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["name", "age", "city"]

    probe_format = model_m7_probe._json_schema_format()
    assert probe_format["json_schema"]["name"] == "person"
    assert probe_format["json_schema"]["strict"] is True


def test_m8_demo_runs_offline_and_shows_the_declarative_registry(capsys):
    """m8 demo 全程离线（不发请求、不需要 key），所以可以整段跑并断言输出。

    M8 的产物是**配置层**，它的行为（加载 / 校验 / 查表 / 报错）不依赖网络——
    这正是这个 demo 不需要 API key 的原因。
    """
    sys.path.insert(0, str(EXAMPLES))
    import model_m8_demo

    model_m8_demo.main()

    out = capsys.readouterr().out
    # 三张随包发布的卡片都列出来了
    assert "deepseek:deepseek-flash" in out
    assert "dashscope:qwen3.7-plus" in out
    assert "zhipu:glm-5.2" in out
    # 坏卡片报错带文件名
    assert "my-card.yaml" in out
    # 未知模型报出可用卡片
    assert "no-such-model" in out
    assert "ALL PARTS DONE" in out
