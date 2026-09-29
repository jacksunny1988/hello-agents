from pydantic import BaseModel


class ChatUsage(BaseModel):
    """一次调用的用量与耗时。

    纯数据容器，刻意不认识任何 SDK 类型——从 completion 里读字段的活儿
    放在 `ChatResponse.from_completion`，避免 SDK 细节渗进这里。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    time: float = 0.0
    cache_read_tokens: int = 0
