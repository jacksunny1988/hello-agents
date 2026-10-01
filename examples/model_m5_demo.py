"""Model M5 示例：取消与重试，端到端

三部分，都走真实的调用或真实的失败：

1. **正常流式**——基线，确认 M5 的改动没打坏 M4 那条路径；
2. **流式中途取消**——策略 B：上层拿到的是 `finished_reason=interrupted` 的完整响应，
   而不是一个 `CancelledError`；**取消前已经产出的正文照样在**；
3. **重试耗尽**——故意指向一个连不上的地址，造出可重试异常，看它退避重试到次数
   耗尽，再抛出**最后一次**的原始异常。具体是哪种异常取决于环境（直连会得到
   `APIConnectionError`，经代理可能得到 5xx 的 `InternalServerError`），两者都在
   默认可重试集合里，所以断言写的是「尝试次数」而不是「异常类型」。

第 1、2 部分需要 .env 里备好对应 provider 的 API key，且**会发起真实网络请求并产生
费用**；第 3 部分只连 127.0.0.1 的 discard 端口，不产生任何费用。

用法：
    python examples/model_m5_demo.py                       # 默认 deepseek
    python examples/model_m5_demo.py zhipu:glm-5.2
"""

import asyncio
import logging
import sys

from hello_agents.model import Message, MissingAPIKeyError
from hello_agents.model._registry import build_client, get_model_config, parse_spec
from hello_agents.model.providers import OpenAICompatModel, build_model

SPEC = "deepseek:deepseek-flash"
PROMPT = "用三句话解释什么是 token"
CANCEL_AFTER_CHARS = 20  # 正文产出这么多字之后再取消，确保是「中途」而非「首片前」
UNREACHABLE = "http://127.0.0.1:9/v1"  # 9 是 discard 端口，必定连不上
RETRIES = 2  # 第 3 部分用：共尝试 RETRIES + 1 次


def _text(response) -> str:
    """把响应里的正文块拼起来（思考块不算）。"""
    return "".join(b.text for b in response.content if b.type == "text")


async def part1_normal(spec: str) -> None:
    """正常流式：基线。"""
    model = build_model(spec, stream=True)
    parts = [p async for p in await model([Message.user(PROMPT)])]

    final = parts[-1]
    assert final.is_last is True, "最后一片必须是完整响应"
    assert final.finished_reason == "completed"

    print(f"  增量 {len(parts) - 1} 帧 → 收尾正文 {len(_text(final))} 字")
    assert final.usage is not None
    print(
        f"  usage: in={final.usage.input_tokens} out={final.usage.output_tokens} "
        f"time={final.usage.time:.2f}s"
    )


async def part2_cancel(spec: str) -> None:
    """流式中途取消：策略 B 下拿到 INTERRUPTED 的完整响应，而不是 CancelledError。"""
    model = build_model(spec, stream=True)
    parts: list = []

    async def consume() -> None:
        async for part in await model([Message.user(PROMPT)]):
            parts.append(part)

    def produced() -> int:
        return sum(len(_text(p)) for p in parts if not p.is_last)

    task = asyncio.create_task(consume())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 30
    while produced() < CANCEL_AFTER_CHARS:
        if task.done():
            raise AssertionError("流在产出足够正文之前就结束了，没法制造中途取消")
        if loop.time() > deadline:
            raise AssertionError("30s 内没等到足够的正文增量")
        await asyncio.sleep(0.01)

    print(f"  已产出正文 {produced()} 字（{len(parts)} 帧），现在取消")
    task.cancel()
    await task  # 策略 B：这里不会抛 CancelledError

    final = parts[-1]
    assert final.is_last is True, "取消后也必须有一个收尾的完整响应"
    assert final.finished_reason == "interrupted", final.finished_reason
    assert task.cancelled() is False, "任务被当成正常完成，而不是 cancelled"
    assert task.cancelling() == 0, "uncancel() 应当把粘性计数还回去"

    already = "".join(_text(p) for p in parts[:-1])
    print(f"  中断前已产出正文 {len(already)} 字，收尾: {final.finished_reason}")
    print(f"  task.cancelled()={task.cancelled()} cancelling()={task.cancelling()}")


async def part3_retry_exhausted(spec: str) -> None:
    """可重试错误 → 退避重试到耗尽 → 抛出最后一次的原始异常。"""

    class _Counting(OpenAICompatModel):
        """只为数次数：重试是否真的发生了，看 `calls` 而不是看日志。"""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.calls = 0

        async def _call_api(
            self, messages, stream, tools=None, tool_choice=None, response_format=None
        ):
            self.calls += 1
            return await super()._call_api(
                messages,
                stream,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
            )

    provider, model = parse_spec(spec)
    cfg = get_model_config(provider, model).model_copy(update={"base_url": UNREACHABLE})
    m = _Counting(cfg, build_client(cfg), max_retries=RETRIES, retry_delay=0.5)

    try:
        await m([Message.user("hi")])
    except Exception as exc:  # noqa: BLE001 — 具体类型随环境变，这里只断言它可重试
        assert isinstance(exc, m._get_retryable_exceptions()), (
            f"{type(exc).__name__} 不在默认可重试集合里，重试循环不该重试它"
        )
        assert m.calls == RETRIES + 1, f"应当尝试 {RETRIES + 1} 次，实际 {m.calls}"
        print(
            f"  尝试 {m.calls} 次后抛出 {type(exc).__name__}（上面应有 {RETRIES} 条重试日志）"
        )
    else:
        raise AssertionError("连不上 127.0.0.1:9，应当抛异常")


PARTS = [
    ("1. 正常流式（基线）", part1_normal),
    ("2. 流式中途取消", part2_cancel),
    ("3. 重试耗尽", part3_retry_exhausted),
]


def main() -> None:
    spec = sys.argv[1] if len(sys.argv) > 1 else SPEC
    # 日志走 stdout：默认的 stderr 与 print 的 stdout 缓冲策略不同，
    # 管道里会把重试日志和分段标题的先后顺序搅乱。
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stdout,
        format="  [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )

    for title, run in PARTS:
        print(f"== {title} | {spec} ==")
        try:
            asyncio.run(run(spec))
        except MissingAPIKeyError as exc:
            print(f"  跳过: {exc}")
        except Exception as exc:  # noqa: BLE001 — 一部分失败不该挡住另两部分
            print(f"  失败: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
