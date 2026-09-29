import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from ._registry import ModelConfig
from ._response import ChatResponse, FinishedReason
from .message import Message


class ChatModelBase(ABC):
    """模型基类（模板方法）。

    子类只实现 `_call_api`（协议相关：怎么发请求、怎么解析），
    `__call__` / `_stream` 里的重试、聚合、收尾都由基类收口，子类不重复实现。

    `stream` 是**构造参数**而不是调用参数：一个实例要么流式要么不流式，
    `__call__` 的返回类型才不会随调用飘忽。
    """

    def __init__(
        self, config: ModelConfig, client: AsyncOpenAI, stream: bool = False
    ) -> None:
        self.config = config
        self.client = client
        self.stream = stream

    async def __call__(
        self, messages: list[Message]
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        if not self.stream:
            return await self._call_api(messages=messages, stream=False)
        return self._stream(messages=messages)

    async def _stream(self, messages: list[Message]) -> AsyncGenerator[ChatResponse]:
        t0 = time.perf_counter()
        acc = ChatResponse(content=[], is_last=True)

        async for delta in await self._call_api(messages=messages, stream=True):
            acc.append_chat_response(delta)
            if delta.content:  # 空 content 即 carrier，不产出
                yield delta

        acc.finished_reason = FinishedReason.COMPLETED
        if acc.usage is not None:
            # 单帧没有耗时概念（见 parse_chunk），总耗时只能在这里补。
            # 含消费方处理时间，比非流式那个纯 API 耗时略宽——可接受。
            acc.usage.time = time.perf_counter() - t0
        yield acc

    @abstractmethod
    async def _call_api(
        self, messages: list[Message], stream: bool
    ) -> ChatResponse | AsyncGenerator[ChatResponse]: ...
