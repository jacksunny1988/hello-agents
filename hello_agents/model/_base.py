import asyncio
import itertools
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import TypeVar

from openai import AsyncOpenAI

from ._registry import ModelConfig
from ._response import ChatResponse, FinishedReason
from ._tool import Tool, ToolChoice
from .message import Message

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ChatModelBase(ABC):
    """模型基类（模板方法）。

    子类只实现 `_call_api`（协议相关：怎么发请求、怎么解析），
    `__call__` / `_stream` 里的重试、取消、聚合、收尾都由基类收口，子类不重复实现。

    `stream` 是**构造参数**而不是调用参数：一个实例要么流式要么不流式，
    `__call__` 的返回类型才不会随调用飘忽。

    取消采用**策略 B**（规格 §1.2）：捕获 `CancelledError` 后不重新抛出，而是返回
    一个 `finished_reason=INTERRUPTED` 的响应，让上层用「处理普通响应」的统一方式
    处理中断。代价有两条，用之前必须知道：

    1. 调用方**不能**再用 `task.cancelled()` 或 `except CancelledError` 判断
       「这次是被取消的」——任务会被当成正常完成，只能读 `finished_reason`。
    2. 包在外层的 `asyncio.timeout()` / `asyncio.wait_for()` 会**彻底失效**：
       我们正常 return，`Timeout.__aexit__` 收到的 `exc_type` 是 `None`，
       直接短路掉 `raise TimeoutError`（CPython `asyncio/timeouts.py:112`）。
       这一条 `uncancel()` 也修不了——想修就得改成策略 A（重新抛出）。
       **要硬性截止时间，请用 `AsyncOpenAI(timeout=...)`**：SDK 抛的
       `APITimeoutError` 是个普通 `Exception`，既不会被策略 B 吞掉，
       本身还落在可重试集合里。
    """

    def __init__(
        self,
        config: ModelConfig,
        client: AsyncOpenAI,
        stream: bool = False,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self.config = config
        self.client = client
        self.stream = stream
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def __call__(
        self,
        messages: list[Message],
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        """非流式返回完整 `ChatResponse`；流式返回增量异步生成器。

        注意两种形态都**先 await**（本方法是 `async def`）：

            model = build_model("deepseek:deepseek-flash")            # 非流式
            response = await model(messages)

            model = build_model("deepseek:deepseek-flash", stream=True)
            stream = await model(messages)                            # 先 await 拿到流
            async for part in stream: ...

        `tools` / `tool_choice` 只做**透传**：工具协议的解释在 Formatter 与上层，
        重试、取消、聚合这些收口逻辑与「这次带不带工具」无关，基类不碰它们。
        """
        if not self.stream:
            try:
                return await self._with_retry(
                    lambda: self._call_api(
                        messages=messages,
                        stream=False,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                )
            except asyncio.CancelledError:
                # 策略 B：吞掉取消，给上层一个可统一处理的收尾对象。
                #
                # uncancel() 必须调：取消计数是**粘性**的，不还回去会污染本任务
                # 后续的 asyncio.timeout() 记账——实测「吞取消发生在 timeout 块内部、
                # 之后 timeout 才到期」时，调用方拿到的是裸 CancelledError 而不是
                # TimeoutError（CPython `asyncio/timeouts.py:112` 的
                # `uncancel() <= self._cancelling` 比较会失败）。
                current = asyncio.current_task()
                if current is not None:
                    current.uncancel()
                return ChatResponse(
                    content=[], finished_reason=FinishedReason.INTERRUPTED
                )
        return self._stream(messages, tools, tool_choice)

    async def _stream(
        self,
        messages: list[Message],
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncGenerator[ChatResponse]:
        """流式聚合收口：逐片产出增量，最后产出一个完整响应。

        产出约定（消费方据此区分过程与结果）：

        - 前面的每一片都是**增量**（`is_last=False`），正文/思考分别累加；
        - 只有 `content` 非空的片才产出——usage 载体片（`choices=[]`）吸收后跳过，
          不让用户看见空帧；
        - 最后一片是**完整响应**（`is_last=True`），文本已拼好、usage 已吸收，
          `finished_reason` 来自末片的 `finish_reason`（如 `TOOL_CALLS`）；
          被取消时是 `INTERRUPTED`。

        **重试只覆盖「取到流之前」**：`await self._call_api(..., stream=True)` 这一步
        含建连与状态码检查（SDK 在 `create()` 里就抛非 2xx），失败可以安全重试。
        一旦进入 `async for` 并 yield 出去，上层可能已经看到部分输出，再「从头重试」
        会重复已发送的内容——所以此后任何异常都**直接上抛**，不自动重试（规格 §4）。

        比规格 §4 的措辞略窄：规格说「拿到首片可见帧之前」，这里是「`create()` 返回
        之前」。差别只在「响应体已开始传输、但首个 content 帧还没到」时的连接中断，
        那种情况不重试——要覆盖它得把首片也拉进重试保护，代价是生成器泄漏与状态
        回滚，不划算。

        累加本身不在这里实现，而是委托给 `ChatResponse.append_chat_response`：
        协议解析在 Formatter、累加在 ChatResponse、驱动在基类，三处各管一段。
        """
        t0 = time.perf_counter()
        acc = ChatResponse(content=[], is_last=True)

        try:
            # 局部标注用来收窄 `_call_api` 的联合返回类型：这里 stream=True，
            # 拿到的必然是异步生成器。仓库没配 mypy，标注主要是给读代码的人看；
            # 要严格推导得给 `_call_api` 加 @overload，但那需要每个子类重复一遍
            # 重载（子类的普通签名会覆盖基类重载），代价远大于收益。
            raw: AsyncGenerator[ChatResponse] = await self._with_retry(
                lambda: self._call_api(
                    messages=messages, stream=True, tools=tools, tool_choice=tool_choice
                )
            )

            async for delta in raw:
                acc.append_chat_response(delta)
                if delta.content:  # 空 content 即 carrier，不产出
                    yield delta
        except asyncio.CancelledError:
            # 策略 B：不重新抛出，把累加器标记为中断后照常收尾，让上层拿到一个
            # 明确的收尾对象而不是空异常。uncancel() 的理由同 `__call__`。
            #
            # 只捕 CancelledError，**不要**扩成 BaseException：消费方 `aclose()` 时
            # 抛进来的是 GeneratorExit，在它的处理中再 yield 会触发
            # `RuntimeError: async generator ignored GeneratorExit`。
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
            acc.finished_reason = FinishedReason.INTERRUPTED

        # 正常收尾**不覆盖** finished_reason：末片的 finish_reason 已经由累加器
        # 吸收（TOOL_CALLS 之类），在这里写回 COMPLETED 会把它冲掉。
        if acc.usage is not None:
            # 单帧没有耗时概念（见 parse_chunk），总耗时只能在这里补。
            # 含消费方处理时间，比非流式那个纯 API 耗时略宽——可接受。
            acc.usage.time = time.perf_counter() - t0
        yield acc

    async def _with_retry(self, afn: Callable[[], Awaitable[T]]) -> T:
        """把 `afn()` 最多执行 `max_retries + 1` 次，成功即返回。

        `afn` 必须是**协程工厂**——每次调用产生一个全新协程。直接传协程对象不行：
        协程只能 await 一次，第二次重试会 `RuntimeError`。

        - 可重试异常（`_get_retryable_exceptions`）→ 记日志、退避、重来；
        - 其它异常立即上抛；
        - 次数耗尽 → 裸 `raise` 重抛**最后一次**的原始异常，traceback 不丢；
        - `CancelledError` 继承 `BaseException`，压根不进 `except Exception`：
          取消不是业务失败，既不会被重试，也不会在这里被吞（吞它的是 `__call__`）。

        无共享状态（全部是局部变量），同一实例并发调用安全。
        """
        retryable = self._get_retryable_exceptions()
        # 无限循环而不是 range(max_retries + 1)：后者在 max_retries 为负时
        # 一次都不执行、直接掉出函数返回 None；配 >= 判断则负数自然退化成「不重试」。
        for attempt in itertools.count():
            try:
                return await afn()
            except Exception as exc:
                if not isinstance(exc, retryable) or attempt >= self.max_retries:
                    raise
                delay = self._backoff_delay(attempt, exc)
                logger.warning(
                    "模型调用失败（第 %d 次，%s），%.1fs 后重试（上限 %d 次）",
                    attempt + 1,
                    type(exc).__name__,
                    delay,
                    self.max_retries,
                )
                await asyncio.sleep(delay)

    def _backoff_delay(self, attempt: int, exc: Exception) -> float:
        header = getattr(getattr(exc, "response", None), "headers", None)
        if header and "retry-after" in header:
            return float(header["retry-after"])
        return self.retry_delay * 2**attempt

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        return (
            APITimeoutError,
            APIConnectionError,
            RateLimitError,
            InternalServerError,
        )

    @abstractmethod
    async def _call_api(
        self,
        messages: list[Message],
        stream: bool,
        tools: Sequence[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> ChatResponse | AsyncGenerator[ChatResponse]: ...
