import asyncio
import threading
from typing import ClassVar

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse, ToolStatus


class SyncAddTool(ToolBase):
    """同步 call：验证自动线程池（应在非主线程执行）。"""

    name = "sync_add"
    description = "同步加法（在线程池执行）"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    }

    def call(self, *, a: float, b: float) -> ToolResponse:
        return ToolResponse.succeed(str(a + b), thread=threading.get_ident())


class SlowTool(ToolBase):
    """异步慢工具：配合 timeout 验证超时。"""

    name = "slow"
    description = "睡眠若干秒"
    timeout: ClassVar[float | None] = 0.2
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"seconds": {"type": "number"}},
        "required": ["seconds"],
    }

    async def call(self, *, seconds: float) -> ToolResponse:
        await asyncio.sleep(seconds)
        return ToolResponse.succeed("done")


class BoomTool(ToolBase):
    """业务异常：验证异常被收口为 fail。"""

    name = "boom"
    description = "总是抛异常"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def call(self) -> ToolResponse:
        raise RuntimeError("something went wrong")


async def main() -> None:
    main_thread = threading.get_ident()

    add = await SyncAddTool()(a=1.0, b=2.0)
    print(
        "sync:",
        add.status is ToolStatus.SUCCESS,
        add.get_text(),
        "off-thread:",
        add.metadata["thread"] != main_thread,
    )

    slow = await SlowTool()(seconds=1)
    print("timeout:", slow.status is ToolStatus.ERROR, slow.get_text())

    no_timeout = SlowTool()
    no_timeout.timeout = None  # 实例级覆盖：不限时
    ok = await no_timeout(seconds=0.05)
    print("no-timeout:", ok.status is ToolStatus.SUCCESS)

    boom = await BoomTool()()
    print("error:", boom.status is ToolStatus.ERROR, boom.get_text())


asyncio.run(main())
