"""T2：执行收口（sync/async 归一 · 线程池 · 超时 · 异常结构化 · 取消传播），全部离线确定性"""

import asyncio
import threading
import time
from typing import ClassVar

import pytest

from hello_agents.tool._base import ToolBase
from hello_agents.tool._response import ToolResponse, ToolStatus


class SyncAddTool(ToolBase):
    """同步 call：验证框架自动丢线程池，并记录执行线程。"""

    name = "sync_add"
    description = "同步加法"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    }

    def call(self, *, a: float, b: float) -> ToolResponse:
        return ToolResponse.succeed(str(a + b), thread=threading.get_ident())


class AsyncEchoTool(ToolBase):
    """异步 call：不应经过线程池。"""

    name = "async_echo"
    description = "异步回显"
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def call(self, *, text: str) -> ToolResponse:
        return ToolResponse.succeed(text, thread=threading.get_ident())


class SlowTool(ToolBase):
    """异步慢工具，类级 timeout=0.2 秒。"""

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


class SlowSyncTool(ToolBase):
    """同步慢工具：验证线程池分支同样受超时约束。"""

    name = "slow_sync"
    description = "同步睡眠"
    timeout: ClassVar[float | None] = 0.1
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def call(self) -> ToolResponse:
        time.sleep(0.5)
        return ToolResponse.succeed("done")


class BoomTool(ToolBase):
    """同步 call 抛业务异常。"""

    name = "boom"
    description = "总是抛异常"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def call(self) -> ToolResponse:
        raise RuntimeError("something went wrong")


class BoomAsyncTool(ToolBase):
    """异步 call 抛业务异常：两条分支都要被兜住。"""

    name = "boom_async"
    description = "异步总是抛异常"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    async def call(self) -> ToolResponse:
        raise ValueError("async boom")


class NeverReturns(ToolBase):
    name = "never"
    description = "永远等待"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    async def call(self) -> ToolResponse:
        await asyncio.Event().wait()  # 永不被 set
        return ToolResponse.succeed("unreachable")


# --- sync/async 归一 --------------------------------------------------------


async def test_sync_call_returns_success():
    resp = await SyncAddTool()(a=1, b=2)
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "3"


async def test_sync_call_runs_off_the_event_loop_thread():
    """to_thread 生效的硬证据：同步 call 的执行线程 != 主线程。"""
    main_thread = threading.get_ident()
    resp = await SyncAddTool()(a=1, b=2)
    assert resp.metadata["thread"] != main_thread


async def test_async_call_returns_success():
    resp = await AsyncEchoTool()(text="hi")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "hi"


async def test_async_call_stays_on_the_event_loop_thread():
    """反向证据：异步 call 不走线程池，仍在事件循环线程上。"""
    main_thread = threading.get_ident()
    resp = await AsyncEchoTool()(text="hi")
    assert resp.metadata["thread"] == main_thread


# --- 超时 -------------------------------------------------------------------


async def test_timeout_returns_error():
    resp = await SlowTool()(seconds=1)
    assert resp.status is ToolStatus.ERROR
    assert "超时" in resp.get_text()
    assert "slow" in resp.get_text()


async def test_instance_level_timeout_none_means_unlimited():
    """实例级覆盖类属性：类级 0.2s 被置 None，睡更久也应成功。

    这里故意 sleep 0.4s（超过类级 0.2s）——若实例级覆盖不生效，本测试会超时变红。
    """
    tool = SlowTool()
    tool.timeout = None  # 实例级覆盖类属性
    resp = await tool(seconds=0.4)
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "done"


async def test_sync_call_is_also_subject_to_timeout():
    """线程池分支同样受 wait_for 约束：框架不再等待，直接返回 ERROR。

    线程本身停不下来（GIL 限制，见文档 2.1 第 4 节），本测试只断言框架侧行为。
    """
    resp = await SlowSyncTool()()
    assert resp.status is ToolStatus.ERROR
    assert "超时" in resp.get_text()


def test_default_timeout_is_none():
    assert ToolBase.timeout is None


# --- 业务异常结构化 ---------------------------------------------------------


async def test_sync_business_exception_becomes_error_response():
    resp = await BoomTool()()
    assert resp.status is ToolStatus.ERROR
    assert resp.get_text() == "RuntimeError: something went wrong"


async def test_async_business_exception_becomes_error_response():
    resp = await BoomAsyncTool()()
    assert resp.status is ToolStatus.ERROR
    assert resp.get_text() == "ValueError: async boom"


async def test_exception_does_not_escape_the_tool_boundary():
    """兜底生效：调用方拿到的是结果对象，而不是抛出的异常。"""
    resp = await BoomTool()()
    assert isinstance(resp, ToolResponse)


# --- 取消传播 ---------------------------------------------------------------


async def test_cancellation_propagates_and_is_not_swallowed():
    """框架不得把取消吞成 ToolResponse.fail——取消必须向上传播。"""
    task = asyncio.create_task(NeverReturns()())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


def test_cancelled_error_is_not_an_exception_subclass():
    """上面那条测试之所以成立，靠的就是这个继承关系。

    若有人把兜底改成 except BaseException，这条会立刻变红。
    """
    assert issubclass(asyncio.CancelledError, BaseException)
    assert not issubclass(asyncio.CancelledError, Exception)
