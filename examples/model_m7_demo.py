"""Model M7 示例：结构化输出三模式横向对比 + 自动修复，端到端

两部分，都走真实网络：

1. **三模式对比**——同一个目标 schema（`Person{name,age,city}`）、同一段输入，
   依次走 `tool` / `json_schema` / `json_object`，打印是否一次通过、修复了几轮、
   input/output token、最终对象；
2. **制造一次修复**——用一条「第一次容易写错、但错误信息足以推出正确写法」的
   用例（电话号带横线 vs `pattern` 要求纯数字），打印每一轮回灌给模型的错误文本。

**真实模型会不会真的先错一次不可控**：想稳定看到修复过程，跑离线测试
`tests/test_model_structured.py`（假模型按脚本返回，确定性）。本 demo 的价值是
**可观测性**——把三种模式的成功率、修复轮数、token 摆在同一张表里。

`json_schema` 一列按卡片能力位决定跑不跑：能力位为 `False` 时直接跳过，不制造
一次注定失败的请求（规格 §4.2 的决策 3）。要把能力位变成事实，跑
`examples/model_m7_probe.py`，再把结论写回 `providers/_models/` 下的 YAML 卡片。

需要 .env 里备好对应 provider 的 API key，**会发起真实网络请求并产生费用**。

用法：
    python examples/model_m7_demo.py                       # 默认 deepseek
    python examples/model_m7_demo.py zhipu:glm-5.2
"""

import asyncio
import logging
import sys

from pydantic import BaseModel, field_validator

from hello_agents.model import (
    Message,
    NativeJsonSchemaUnsupportedError,
    StructuredOutputError,
    extract_structured_with_stats,
)
from hello_agents.model.providers import build_model

# 控制台是 GBK：traceback 与 logging 走 stderr 时中文会乱码，统一导到 stdout。
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)

SPEC = "deepseek:deepseek-flash"
MODES: tuple[str, ...] = ("tool", "json_schema", "json_object")

EXTRACT_PROMPT = "从这句话里抽取人物信息：老张今年三十岁，住在西安。"
PHONE_PROMPT = "从这句话里抽取联系方式：老张的电话是 138-1234-5678。"


class Person(BaseModel):
    """目标 schema：与规格 §7 一致的三字段。"""

    name: str
    age: int
    city: str


class Contact(BaseModel):
    """第 2 部分用：约束写在 `field_validator` 里，**不出现在 JSON Schema 中**。

    这正是「客户端校验是最后一道防线」的场景：模型看到的只是 `{"type":"string"}`，
    它大概率照着原文写成 `138-1234-5678`，然后被客户端拦下、回灌错误、自己改对。
    若把约束写成 `Field(pattern=...)`，模型在 schema 里就看得到，第一次多半直接写对，
    反而演示不到修复闭环。
    """

    name: str
    phone: str

    @field_validator("phone")
    @classmethod
    def _digits_only(cls, value: str) -> str:
        if not (value.isdigit() and len(value) == 11):
            raise ValueError("必须是 11 位纯数字（去掉横线、空格与国家码）")
        return value


async def part1_compare_modes(spec: str) -> None:
    """同一目标、同一输入，三种模式横向对比。"""
    print(f"input: {EXTRACT_PROMPT}")
    print(f"{'mode':<12} {'first':<6} {'rounds':<7} {'in':<6} {'out':<6} result")
    print("-" * 88)

    for mode in MODES:
        model = build_model(spec)
        try:
            person, stats = await extract_structured_with_stats(
                model, [Message.user(EXTRACT_PROMPT)], Person, mode=mode
            )
        except NativeJsonSchemaUnsupportedError as exc:
            print(f"{mode:<12} skipped  (supports_native_json_schema=False)")
            print(f"{'':<12} {exc}")
            continue
        except StructuredOutputError as exc:
            print(f"{mode:<12} FAILED   {exc}")
            continue

        usage = stats.usage
        print(
            f"{mode:<12} {stats.ok_first_try!s:<6} {stats.rounds:<7} "
            f"{usage.input_tokens:<6} {usage.output_tokens:<6} {person!r}"
        )


async def part2_repair_loop(spec: str) -> None:
    """打印每一轮的错误文本：ValidationError 是怎么被回灌、模型又是怎么补全的。"""
    print(f"input: {PHONE_PROMPT}")
    model = build_model(spec)

    try:
        contact, stats = await extract_structured_with_stats(
            model, [Message.user(PHONE_PROMPT)], Contact, mode="tool"
        )
    except StructuredOutputError as exc:
        print(f"修复轮数耗尽：{exc}")
        return

    for attempt in stats.attempts:
        if attempt.error is None:
            print(f"[round {attempt.index}] OK")
        else:
            print(f"[round {attempt.index}] 回灌给模型的错误：")
            print(attempt.error)

    print(f"结果：{contact!r}（修复 {stats.rounds} 轮）")
    if stats.ok_first_try:
        print(
            "这次模型一次就写对了（真实模型不可控）。"
            "想看确定性的修复过程，跑 tests/test_model_structured.py。"
        )


async def main(spec: str) -> None:
    print("=" * 88)
    print("[1] 三模式横向对比")
    await part1_compare_modes(spec)

    print("=" * 88)
    print("[2] 自动修复：观察 ValidationError 回灌")
    await part2_repair_loop(spec)

    print("=" * 88)
    print("ALL PARTS DONE")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else SPEC))
