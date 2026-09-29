import time
from collections.abc import AsyncGenerator

from .._base import ChatModelBase
from .._formatter import parse_chunk, to_openai_messages
from .._registry import build_client, get_model_config, parse_spec
from .._response import ChatResponse
from ..message import Message


class OpenAICompatModel(ChatModelBase):
    async def _call_api(
        self, messages: list[Message], stream: bool
    ) -> ChatResponse | AsyncGenerator[ChatResponse]:
        """三家共用：同一份请求代码，只靠 `self.config` 区分。

        非流式返回完整响应；流式返回「增量响应」的异步生成器。
        """
        openai_msgs = to_openai_messages(messages)

        if not stream:
            t0 = time.perf_counter()
            completion = await self.client.chat.completions.create(
                model=self.config.model,
                messages=openai_msgs,
                stream=False,
            )
            return ChatResponse.from_completion(completion, time.perf_counter() - t0)

        # stream=True 时 create() 返回的是流本身，内容在随后的 async for 里逐片到来。
        # include_usage 必须显式开：流式默认不返回 usage，末片就没有那个载体帧。
        raw_stream = await self.client.chat.completions.create(
            model=self.config.model,
            messages=openai_msgs,
            stream=True,
            stream_options={"include_usage": True},
        )

        async def gen() -> AsyncGenerator[ChatResponse]:
            # parse_chunk 恒返回增量——载体帧也返回，只是 content 为空——
            # 所以这里不用判 None；空帧由基类的 `if delta.content` 挡掉。
            async for chunk in raw_stream:
                yield parse_chunk(chunk)

        return gen()


def build_model(spec: str, stream: bool = False) -> OpenAICompatModel:
    provider, model = parse_spec(spec)
    cfg = get_model_config(provider, model)
    return OpenAICompatModel(cfg, build_client(cfg), stream=stream)
