import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from ._response import ToolResponse

if TYPE_CHECKING:
    from ._base import ToolBase


async def _run_call(tool: "ToolBase", **kwargs: Any) -> ToolResponse:
    """把工具的 call（同步或异步）归一为一次 await，同步 call 丢线程池。"""
    call = tool.call
    if inspect.iscoroutinefunction(call):
        return await call(**kwargs)
    return await asyncio.to_thread(call, **kwargs)


async def execute_tool(
    tool: "ToolBase",
    kwargs: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> ToolResponse:
    """执行单个工具的统一收口。

    - timeout 为秒；None 表示不限（wait_for 接受 None）；
    - 超时      → ToolResponse.fail（status=ERROR）；
    - 业务异常  → ToolResponse.fail（文本含异常类型与信息）；
    - CancelledError 不捕获、直接向上传播。
    """
    kwargs = kwargs or {}
    try:
        return await asyncio.wait_for(
            _run_call(tool, **kwargs),
            timeout=timeout,
        )
    except TimeoutError:
        # asyncio.wait_for 超时抛内置 TimeoutError（3.11+ 与 asyncio.TimeoutError 同义）
        return ToolResponse.fail(f"工具 {tool.name!r} 在 {timeout} 秒后超时")
    except Exception as exc:  # noqa: BLE001
        # T2 契约：业务异常一律兜底转 fail；CancelledError 继承 BaseException，不会落到这里
        return ToolResponse.fail(f"{type(exc).__name__}: {exc}")
