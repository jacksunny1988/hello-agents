import asyncio
import sys

from hello_agents.model import Message, MissingAPIKeyError
from hello_agents.model.providers import build_model

SPECS = [
    "deepseek:deepseek-flash",
    "dashscope:qwen3.7-plus",
    "zhipu:glm-5.2",
]

PROMPT = "用一句话解释什么是 token"


async def ask(spec: str) -> None:
    model = build_model(spec)
    response = await model([Message.user(PROMPT)])
    usage = response.usage

    print(f"== {spec} ==")
    print(f"  模型  : {model.config.model}")
    print(f"  回复  : {''.join(b.text for b in response.content)}")
    if usage is not None:
        print(
            f"  usage : in={usage.input_tokens} out={usage.output_tokens} "
            f"cache={usage.cache_read_tokens} time={usage.time:.2f}s"
        )
    print(f"  收尾  : {response.finished_reason} | is_last={response.is_last}")


def main() -> None:
    specs = sys.argv[1:] or SPECS
    for spec in specs:
        try:
            asyncio.run(ask(spec))
        except MissingAPIKeyError as exc:
            print(f"== {spec} ==\n  跳过: {exc}")
        except Exception as exc:  # noqa: BLE001 — 一家失败不该挡住另两家
            print(f"== {spec} ==\n  失败: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
