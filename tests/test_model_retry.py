"""ChatModelBase 的重试与取消收口（M5）：全部离线，不发网络请求"""

import asyncio
from typing import cast

import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from hello_agents.model import (
    ChatModelBase,
    ChatResponse,
    ChatUsage,
    FinishedReason,
    Message,
    Provider,
    TextBlock,
    build_client,
    get_model_config,
)
from hello_agents.model.providers import OpenAICompatModel


class _Boom(Exception):
    """自定义「可重试」异常：让循环逻辑的测试不依赖 openai 异常的构造。"""


class _FakeModel(ChatModelBase):
    """假模型：`_call_api` 按脚本抛异常或返回，并记录调用次数。

    覆盖 `_get_retryable_exceptions` 只认 `_Boom`，顺带验证「可重试集合
    可被子类覆盖」这个扩展点。
    """

    def __init__(self, outcomes, **kwargs):
        kwargs.setdefault("max_retries", 3)
        kwargs.setdefault("retry_delay", 0)
        super().__init__(
            config=get_model_config(Provider.DEEPSEEK),
            client=cast(AsyncOpenAI, None),
            **kwargs,
        )
        self.outcomes = list(outcomes)
        self.calls = 0

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
        return (_Boom,)

    async def _call_api(self, messages, stream, tools=None, tool_choice=None):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else ChatResponse(content=[])
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _BlockingModel(_FakeModel):
    """`_call_api` 永远挂起，用来制造一个稳定的取消窗口。"""

    async def _call_api(self, messages, stream, tools=None, tool_choice=None):
        self.calls += 1
        await asyncio.Event().wait()
        return ChatResponse(content=[])


# --- 重试 ---


@pytest.mark.asyncio
async def test_retries_until_success():
    """可重试异常 → 自动重试到成功。"""
    model = _FakeModel([_Boom(), _Boom(), ChatResponse(content=[])])

    resp = await model([Message.user("hi")])

    assert isinstance(resp, ChatResponse)
    assert resp.finished_reason is FinishedReason.COMPLETED
    assert model.calls == 3


@pytest.mark.asyncio
async def test_exhausted_raises_last_exception():
    """重试耗尽 → 抛出**最后一次**的原始异常，共试 max_retries + 1 次。"""
    model = _FakeModel(
        [_Boom("第一次"), _Boom("第二次"), _Boom("第三次")], max_retries=2
    )

    with pytest.raises(_Boom) as excinfo:
        await model([Message.user("hi")])

    assert str(excinfo.value) == "第三次"
    assert model.calls == 3


@pytest.mark.asyncio
async def test_non_retryable_raises_immediately():
    """不可重试异常 → 立即抛出，一次都不多试。"""
    model = _FakeModel([ValueError("参数错了"), ChatResponse(content=[])])

    with pytest.raises(ValueError):
        await model([Message.user("hi")])

    assert model.calls == 1


@pytest.mark.asyncio
async def test_success_does_not_retry():
    """一次成功 → 只调用一次。"""
    model = _FakeModel([ChatResponse(content=[])])

    await model([Message.user("hi")])

    assert model.calls == 1


@pytest.mark.asyncio
async def test_backoff_hook_called_with_attempt_index():
    """退避钩子按 attempt 递增（0, 1）被调用——换指数退避只需覆盖它。"""
    seen: list[int] = []

    class _Model(_FakeModel):
        def _backoff_delay(self, attempt: int, exc: Exception) -> float:
            seen.append(attempt)
            return 0

    model = _Model([_Boom(), _Boom(), ChatResponse(content=[])])

    await model([Message.user("hi")])

    assert seen == [0, 1]


@pytest.mark.asyncio
async def test_max_retries_zero_means_single_attempt():
    """max_retries=0 → 只试一次。"""
    model = _FakeModel([_Boom()], max_retries=0)

    with pytest.raises(_Boom):
        await model([Message.user("hi")])

    assert model.calls == 1


@pytest.mark.asyncio
async def test_negative_max_retries_degrades_to_no_retry():
    """max_retries 为负 → 退化成「不重试」，既不崩也不静默返回 None。"""
    model = _FakeModel([_Boom()], max_retries=-1)

    with pytest.raises(_Boom):
        await model([Message.user("hi")])

    assert model.calls == 1


# --- 取消 ---


@pytest.mark.asyncio
async def test_cancel_returns_interrupted_without_retry():
    """真 task.cancel() → 拿到 INTERRUPTED 响应，不重试，且不残留取消计数。"""
    model = _BlockingModel([])
    task = asyncio.create_task(model([Message.user("hi")]))
    await asyncio.sleep(0)  # 让任务真正挂到 await 点上
    task.cancel()

    resp = await task  # 策略 B：不抛 CancelledError，正常返回

    assert resp.finished_reason is FinishedReason.INTERRUPTED
    assert resp.content == []
    assert model.calls == 1  # 取消不参与重试
    assert task.cancelled() is False  # 任务被当成正常完成
    assert task.cancelling() == 0  # uncancel() 已把粘性计数还回去


@pytest.mark.asyncio
async def test_cancelled_error_is_not_retried():
    """CancelledError 继承 BaseException，不落进 `except Exception`，直接穿出去。"""
    calls = 0

    async def afn():
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    model = _FakeModel([])

    with pytest.raises(asyncio.CancelledError):
        await model._with_retry(afn)

    assert calls == 1


# --- 流式（§5.4）---

_HANG = object()  # 脚本哨兵：让生成器停在这里挂起，好制造取消窗口


class _StreamingFake(ChatModelBase):
    """`_call_api` 返回异步生成器，脚本分别控制「建连阶段」与「产出阶段」的失败。

    脚本元素：

    - 异常 → 在 `_call_api` 里抛出，即**建连阶段**失败（应当被重试）；
    - 列表 → 这一轮流的帧序列，其中异常在生成器体内抛出，即**已开始产出后**
      失败（应当直接上抛）；`_HANG` 表示在这里挂起。
    """

    def __init__(self, script, **kwargs):
        kwargs.setdefault("stream", True)  # 必须：否则 __call__ 走非流式分支
        kwargs.setdefault("max_retries", 3)
        kwargs.setdefault("retry_delay", 0)
        super().__init__(
            config=get_model_config(Provider.DEEPSEEK),
            client=cast(AsyncOpenAI, None),
            **kwargs,
        )
        self.script = list(script)
        self.calls = 0

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
        return (_Boom,)

    async def _call_api(self, messages, stream, tools=None, tool_choice=None):
        self.calls += 1
        body = self.script.pop(0) if self.script else []
        if isinstance(body, BaseException):
            raise body

        async def gen():
            for item in body:
                if item is _HANG:
                    await asyncio.Event().wait()
                elif isinstance(item, BaseException):
                    raise item
                else:
                    yield item

        return gen()


def _delta(text: str) -> ChatResponse:
    """一个正文增量帧。"""
    return ChatResponse(content=[TextBlock(text=text, id="")], is_last=False)


def _carrier(input_tokens: int = 7, output_tokens: int = 9) -> ChatResponse:
    """usage 载体帧：content 为空、只带 usage。"""
    return ChatResponse(
        content=[],
        usage=ChatUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        is_last=False,
    )


@pytest.mark.asyncio
async def test_stream_retries_when_connection_fails():
    """建连阶段失败 → 重试；重试成功后正常产出并收尾。"""
    model = _StreamingFake([_Boom(), [_delta("你"), _delta("好")]])

    parts = [p async for p in await model([Message.user("hi")])]

    assert model.calls == 2
    assert [p.is_last for p in parts] == [False, False, True]
    final = parts[-1]
    assert final.finished_reason is FinishedReason.COMPLETED
    assert "".join(b.text for b in final.content) == "你好"
    assert final.usage is None  # 没有载体帧就没有 usage


@pytest.mark.asyncio
async def test_stream_failure_after_first_delta_is_not_retried():
    """已产出增量后中途失败 → 直接上抛，绝不从头重试（否则内容会重复）。"""
    model = _StreamingFake([[_delta("已经发出去的"), ValueError("连接断了")]])

    stream = await model([Message.user("hi")])
    parts = []
    with pytest.raises(ValueError):
        async for part in stream:
            parts.append(part)

    assert [b.text for p in parts for b in p.content] == ["已经发出去的"]
    assert model.calls == 1  # 一次都不重试


@pytest.mark.asyncio
async def test_stream_absorbs_usage_carrier():
    """usage 载体帧被吸收，不作为可见空帧产出。"""
    model = _StreamingFake([[_delta("答"), _carrier(7, 9)]])

    parts = [p async for p in await model([Message.user("hi")])]

    assert len(parts) == 2  # 只有「增量 + 收尾」，载体帧没产出
    final = parts[-1]
    assert final.is_last is True
    assert final.usage is not None
    assert (final.usage.input_tokens, final.usage.output_tokens) == (7, 9)
    assert final.usage.time > 0  # 收尾补上了总耗时


@pytest.mark.asyncio
async def test_stream_cancel_yields_interrupted_final():
    """流式取消 → 收尾的完整响应是 INTERRUPTED，且不残留取消计数。"""
    model = _StreamingFake([[_delta("半个答"), _HANG]])
    parts = []

    async def consume():
        async for part in await model([Message.user("hi")]):
            parts.append(part)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)  # 等它真的挂进生成器
    task.cancel()
    await task  # 策略 B：不抛 CancelledError

    assert [b.text for p in parts if not p.is_last for b in p.content] == ["半个答"]
    final = parts[-1]
    assert final.is_last is True
    assert final.finished_reason is FinishedReason.INTERRUPTED
    assert task.cancelled() is False
    assert task.cancelling() == 0


@pytest.mark.asyncio
async def test_stream_cancel_before_first_delta_yields_interrupted_final():
    """首片之前就被取消 → 也要产出 INTERRUPTED 的收尾对象，而不是空异常。"""
    model = _StreamingFake([[_HANG]])
    parts = []

    async def consume():
        async for part in await model([Message.user("hi")]):
            parts.append(part)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    task.cancel()
    await task

    assert len(parts) == 1
    assert parts[0].is_last is True
    assert parts[0].finished_reason is FinishedReason.INTERRUPTED


# --- 接线 ---


def test_default_retryable_set_matches_spec_table():
    """默认可重试集合与规格 §2 表格一致（5xx 全覆盖，有意不含 408/409）。"""
    assert set(ChatModelBase._get_retryable_exceptions()) == {
        APITimeoutError,
        APIConnectionError,
        RateLimitError,
        InternalServerError,
    }


def test_openai_compat_model_inherits_default_retryable_set():
    """具体的 provider 类没有另起一套，用的是基类声明的集合。"""
    assert OpenAICompatModel._get_retryable_exceptions() == (
        ChatModelBase._get_retryable_exceptions()
    )


def test_build_client_disables_sdk_retry(monkeypatch):
    """build_client 关掉 SDK 自带重试，重试语义只由 _with_retry 负责。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    client = build_client(get_model_config(Provider.DEEPSEEK))

    assert client.max_retries == 0
