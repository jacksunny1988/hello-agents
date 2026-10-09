import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ._response import ToolResponse, ToolStatus

if TYPE_CHECKING:
    from ._base import ToolBase
    from ._toolkit import Toolkit


class ToolHook:
    """前后 Hook 基类：子类只覆盖关心的方法。"""

    def before(self, tool: "ToolBase", kwargs: dict[str, Any]) -> dict[str, Any] | None:
        """调用前运行；返回新 kwargs 覆盖，返回 None 表示不变。"""
        return None

    def after(
        self, tool: "ToolBase", kwargs: dict[str, Any], response: ToolResponse
    ) -> None:
        """调用后运行；只读审计，返回值被忽略。"""
        return


class Approver(ABC):
    @abstractmethod
    async def approve(self, tool: "ToolBase", kwargs: dict[str, Any]) -> bool:
        """在工具调用前执行的审批逻辑。"""
        ...


class AutoApprover(Approver):
    def __init__(self, allow: bool) -> None:
        self._allow = allow

    async def approve(self, tool: "ToolBase", kwargs: dict[str, Any]) -> bool:
        """自动审批，始终返回 True。"""
        return self._allow


class ConsoleApprover(Approver):
    async def approve(self, tool: "ToolBase", kwargs: dict[str, Any]) -> bool:
        """在控制台中提示用户进行审批。"""
        print(f"[审批] 工具 {tool.name!r} 请求执行，参数：{kwargs}")
        answer = await asyncio.to_thread(input, "允许执行？(y/n): ")
        return answer.strip().lower() in ("y", "yes")


def needs_confirmation(tool: "ToolBase", kwargs: dict[str, Any]) -> bool:
    """三态规则：None→非只读需确认；True→总是；False→从不。"""
    flag = tool.requires_confirmation
    if flag is None:
        return not tool.is_read_only
    return flag


def _backoff(attempt: int, base: float) -> float:
    return base * (2**attempt)


async def run_governed(
    toolkit: "Toolkit",
    tool: "ToolBase",
    kwargs: dict[str, Any],
) -> ToolResponse:
    """在 Toolkit 门面编排：before → 确认 → 重试 → after。"""
    # 1) before hooks（可改 kwargs）
    for hook in toolkit.hooks:
        updated = hook.before(tool, kwargs)
        if updated is not None:
            kwargs = updated

    # 2) 人工确认
    if needs_confirmation(tool, kwargs):
        approver = toolkit.approver
        if approver is None:
            # 配置错误：需要授权却没有审批者，绝不默认放行
            raise RuntimeError(
                f"工具 {tool.name!r} 需要人工确认，但 Toolkit 未配置 approver"
            )
        if not await approver.approve(tool, kwargs):
            response = ToolResponse.denied(f"用户拒绝执行 {tool.name}")
            for hook in toolkit.hooks:
                hook.after(tool, kwargs, response)
            return response

    # 3) 执行 + 重试（只重试 ERROR）
    attempt = 0
    while True:
        response = await tool(**kwargs)
        if response.status is not ToolStatus.ERROR or attempt >= tool.max_retries:
            break
        await asyncio.sleep(_backoff(attempt, tool.retry_backoff))
        attempt += 1

    # 4) after hooks
    for hook in toolkit.hooks:
        hook.after(tool, kwargs, response)
    return response
