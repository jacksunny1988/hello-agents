"""Model M7 探测：三家原生 strict json_schema 支持情况（规格 §8.1）

M2 把 `supports_native_json_schema` 保守设成 `False`，本脚本负责把它变成**事实**：
对三家各发一次请求，记录被接受还是被拒绝，最后打印可直接粘回
`hello_agents/model/_registry.py` 的结论表。

**为什么不用 `extract_structured(mode="json_schema")` 探测**：那个入口在能力位为
`False` 时会先抛 `NativeJsonSchemaUnsupportedError`——正是我们要探测的东西，
用它探测是循环论证。这里直接调模型层，让端点的原始错误露出来。

三种结果要分清（这是本脚本最容易写错的地方）：

| 状态 | 含义 | 能力位 |
|---|---|---|
| `rejected` | 端点报错（如 400 response_format unavailable） | False |
| `ignored`  | 端点**接受**参数但**不遵守**，返回的还是散文 | **False** |
| `honored`  | 返回合法 JSON | True |

`ignored` 是最坏的一种：请求不报错，输出却不是 JSON，客户端只能靠修复闭环兜底。
按 `startswith("OK")` 判定会把它错判成 True——本脚本第一版就踩了这个坑。

**两个 prompt 是刻意的**：`json_schema` 用**不提 JSON** 的 prompt，约束才只能来自
服务端参数——若 prompt 里写了「以 JSON 返回」，模型照做，就分不清是服务端约束生效
还是提示词生效了。`json_object` 反过来必须提 JSON：协议要求 prompt 里出现这个词
（否则三家都回 400 `must contain the word 'json'`），那是参数本身的前提。

纪律：

- 一家失败不影响下一家（逐家 `try/except Exception`）；
- 没配 key 的 provider 直接跳过，不中断；
- 每家只打印**形状**（能否解析、字段名列表），不打印全文——脚本的目的是能力位，
  不是看模型说了什么。

用法（在仓库根目录）：
    ./.venv/Scripts/python.exe examples/model_m7_probe.py
    ./.venv/Scripts/python.exe examples/model_m7_probe.py dashscope:qwen3.7-plus
"""

import asyncio
import json
import logging
import sys

from pydantic import BaseModel

from hello_agents.model import (
    Message,
    MissingAPIKeyError,
    Provider,
    get_api_key,
    get_model_config,
)
from hello_agents.model._structured import strict_json_schema
from hello_agents.model.providers import build_model

# 控制台是 GBK：traceback 与 logging 走 stderr 时中文会乱码，统一导到 stdout。
logging.basicConfig(stream=sys.stdout, level=logging.WARNING)

DEFAULT_SPECS = ["dashscope:qwen3.7-plus", "deepseek:deepseek-flash", "zhipu:glm-5.2"]

# json_schema 探测：**刻意不提 JSON**，否则分不清是服务端约束还是提示词在起作用。
SCHEMA_PROMPT = "从这句话里抽取人物：老张今年三十岁，住在西安。"
# json_object 探测：协议要求 prompt 里出现 json 这个词，否则端点直接 400。
OBJECT_PROMPT = "把这句话里的人物抽成 JSON：老张今年三十岁，住在西安。"


class Person(BaseModel):
    """探测用最小 schema：字段少、无嵌套，失败原因才归因得清。"""

    name: str
    age: int
    city: str


def _json_object_format() -> dict:
    return {"type": "json_object"}


def _json_schema_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "person",
            "schema": strict_json_schema(Person),
            "strict": True,
        },
    }


async def _probe(spec: str, response_format: dict, prompt: str) -> tuple[str, str]:
    """发一次请求，返回 `(状态, 说明)`。状态见模块 docstring 的三种取值。"""
    model = build_model(spec)
    try:
        response = await model([Message.user(prompt)], response_format=response_format)
    except Exception as exc:  # noqa: BLE001 — 探测脚本要的就是「任何失败」
        detail = str(exc).strip().splitlines()
        return "rejected", f"{type(exc).__name__}: {detail[0] if detail else ''}"

    text = "".join(
        block.text for block in response.content if getattr(block, "text", None)
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "ignored", f"端点没报错，但返回的不是 JSON（前 60 字：{text[:60]!r}）"

    fields = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
    return "honored", f"字段={fields}"


async def _probe_provider(spec: str) -> bool | None:
    """返回建议的注册表取值；缺 key 跳过时返回 None。"""
    provider = Provider(spec.split(":", 1)[0])
    try:
        get_api_key(provider)
    except MissingAPIKeyError:
        print(f"--- {spec}: skipped（缺少 {provider.value} 的 API key）")
        return None

    config = get_model_config(provider)
    print(
        f"--- {spec}（注册表现值 "
        f"supports_native_json_schema={config.supports_native_json_schema}）"
    )

    object_status, object_detail = await _probe(
        spec, _json_object_format(), OBJECT_PROMPT
    )
    print(f"    json_object  : {object_status:<9} {object_detail}")

    schema_status, schema_detail = await _probe(
        spec, _json_schema_format(), SCHEMA_PROMPT
    )
    print(f"    json_schema  : {schema_status:<9} {schema_detail}")

    supported = schema_status == "honored"
    if schema_status == "ignored":
        print("    ！端点接受参数但不遵守它——按 False 处理，比报错更坏的一种")
    print(f"    => 建议 supports_native_json_schema={supported}")
    return supported


async def main(specs: list[str]) -> None:
    print("=" * 72)
    print("M7 探测：原生 strict json_schema 支持情况")
    print("=" * 72)

    results: dict[str, bool | None] = {}
    for spec in specs:
        results[spec] = await _probe_provider(spec)

    print("=" * 72)
    print(
        "可直接粘回 _registry.py 的结论（注意：json_object 通不代表 json_schema 通）："
    )
    for spec, supported in results.items():
        if supported is None:
            print(f"  {spec:<26} 未探测（缺 key）")
        else:
            print(f"  {spec:<26} supports_native_json_schema={supported}")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_SPECS))
