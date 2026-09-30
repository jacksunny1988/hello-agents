"""Model M4 示例：流式聚合，三家通

和 M3 一样用**同一套代码**打三家，只换 `spec` 字符串；区别是这次 `stream=True`，
`await model(...)` 拿到的是**增量异步生成器**：思考链一种样式、正文另一种样式
边到边打印，流结束后再拿到拼好的完整 `ChatResponse`。

需要 .env 里备好 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / ZHIPU_API_KEY；
**会发起真实网络请求并产生费用**。

用法：
    python examples/model_m4_stream_demo.py                # 三家都打
    python examples/model_m4_stream_demo.py deepseek       # 只打一家
    python examples/model_m4_stream_demo.py zhipu:glm-5.2  # 指定到模型
"""

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


def styles(color: bool) -> tuple[str, str, str]:
    """(思考色, 正文色, 复位)。不是终端就全部退化成空串，输出照样可读。"""
    if not color:
        return "", "", ""
    return "\x1b[2m", "\x1b[36m", "\x1b[0m"


async def ask(spec: str, color: bool) -> None:
    model = build_model(spec, stream=True)
    print(f"== {spec} ==")
    print(f"  模型  : {model.config.model}")

    dim, cyan, reset = styles(color)
    # 注意要 await：__call__ 是 async def，两种形态都先 await 再分叉
    stream = await model([Message.user(PROMPT)])

    kind: str | None = None  # 当前正在流的块类型，用来在思考/正文之间换行
    visible_text: list[str] = []
    final = None

    async for part in stream:
        if part.is_last:  # 收尾的完整响应，不属于「过程增量」
            final = part
            continue

        for block in part.content:
            if block.type != kind:
                if kind is not None:
                    print()
                is_thinking = block.type == "thinking"
                label = "思考" if is_thinking else "正文"
                print(
                    f"  {dim if is_thinking else cyan}[{label}]{reset} ",
                    end="",
                    flush=True,
                )
                kind = block.type

            text = block.text if block.type == "text" else block.thinking
            print(
                f"{dim if block.type == 'thinking' else cyan}{text}{reset}",
                end="",
                flush=True,
            )
            if block.type == "text":
                visible_text.append(text)

    print()
    assert final is not None, "流必须以一个 is_last=True 的完整响应收尾"

    # 自检：完整结果里的正文 == 所有增量拼起来（规格 §5 第 3 条）
    final_text = "".join(b.text for b in final.content if b.type == "text")
    joined = "".join(visible_text)
    ok = "✓" if final_text == joined else "✗"
    print(f"  拼接  : {ok} 增量拼出的正文 == 完整响应的正文（{len(joined)} 字）")

    usage = final.usage
    if usage is None:
        # include_usage 没开（或服务端没给）时的兜底路径：不该崩
        print("  usage : 缺失（服务端没回载体帧）")
    else:
        print(
            f"  usage : in={usage.input_tokens} out={usage.output_tokens} "
            f"cache={usage.cache_read_tokens} time={usage.time:.2f}s"
        )
        # qwen 这类推理模型的 output_tokens 远大于可见正文——差额就是思考链
        print(
            f"  对比  : output_tokens={usage.output_tokens} "
            f"vs 可见正文 {len(joined)} 字"
        )
    blocks = [
        (b.type, len(b.text if b.type == "text" else b.thinking)) for b in final.content
    ]
    print(
        f"  收尾  : {final.finished_reason} | is_last={final.is_last} | 块(类型,长度)={blocks}"
    )


def main() -> None:
    specs = sys.argv[1:] or SPECS
    color = sys.stdout.isatty()
    for spec in specs:
        try:
            asyncio.run(ask(spec, color))
        except MissingAPIKeyError as exc:
            print(f"== {spec} ==\n  跳过: {exc}")
        except Exception as exc:  # noqa: BLE001 — 一家失败不该挡住另两家
            print(f"== {spec} ==\n  失败: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
