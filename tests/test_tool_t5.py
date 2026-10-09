"""T5：横切治理（前后 Hook · 人工确认 · 工具层重试），全部离线确定性"""

from typing import Any, ClassVar

import pytest

from hello_agents.tool._base import ToolBase
from hello_agents.tool._governance import (
    Approver,
    AutoApprover,
    ToolHook,
    needs_confirmation,
)
from hello_agents.tool._response import ToolResponse, ToolStatus
from hello_agents.tool._toolkit import Toolkit

# --- 被测工具 ---------------------------------------------------------------


class ReadTool(ToolBase):
    """只读：默认免确认。记录收到的参数，用来验证 Hook 改写。"""

    name = "read"
    description = "只读工具"
    is_read_only = True
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self) -> None:
        super().__init__()
        self.received: list[str] = []

    async def call(self, *, text: str) -> ToolResponse:
        self.received.append(text)
        return ToolResponse.succeed(text)


class WriteTool(ToolBase):
    """写操作：默认需确认。"""

    name = "write"
    description = "写入"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def call(self) -> ToolResponse:
        self.calls += 1
        return ToolResponse.succeed("written")


class AlwaysAskReadTool(ToolBase):
    """只读，但显式要求确认。"""

    name = "read_ask"
    description = "只读但强制确认"
    is_read_only = True
    requires_confirmation: ClassVar[bool] = True
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    async def call(self) -> ToolResponse:
        return ToolResponse.succeed("ok")


class SilentWriteTool(ToolBase):
    """写操作，但显式免确认。"""

    name = "write_silent"
    description = "写入但免确认"
    requires_confirmation: ClassVar[bool] = False
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    async def call(self) -> ToolResponse:
        return ToolResponse.succeed("written")


class FlakyTool(ToolBase):
    """前 fail_times 次返回 ERROR，之后成功。"""

    name = "flaky"
    description = "不稳定工具"
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(
        self, *, fail_times: int, max_retries: int = 0, backoff: float = 0.0
    ) -> None:
        super().__init__()
        self.fail_times = fail_times
        self.max_retries = max_retries
        self.retry_backoff = backoff
        self.calls = 0

    async def call(self) -> ToolResponse:
        self.calls += 1
        if self.calls <= self.fail_times:
            return ToolResponse.fail("transient")
        return ToolResponse.succeed("ok")


class DeniedTool(ToolBase):
    """总是返回 DENIED，用来验证 DENIED 不触发重试。"""

    name = "denied_tool"
    description = "总是拒绝"
    requires_confirmation: ClassVar[bool] = False
    input_schema: ClassVar[dict] = {"type": "object", "properties": {}}

    def __init__(self, *, max_retries: int = 2) -> None:
        super().__init__()
        self.max_retries = max_retries
        self.calls = 0

    async def call(self) -> ToolResponse:
        self.calls += 1
        return ToolResponse.denied("nope")


# --- 测试替身 ---------------------------------------------------------------


class RecordingHook(ToolHook):
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def before(self, tool: ToolBase, kwargs: dict[str, Any]) -> None:
        self.events.append(("before", tool.name))

    def after(
        self, tool: ToolBase, kwargs: dict[str, Any], response: ToolResponse
    ) -> None:
        self.events.append(("after", response.get_text()))


class RewritingHook(ToolHook):
    """before 返回新 dict，应当覆盖 kwargs。"""

    def before(self, tool: ToolBase, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {**kwargs, "text": "rewritten"}


class OrderHook(ToolHook):
    def __init__(self, label: str, log: list[str]) -> None:
        self.label = label
        self.log = log

    def before(self, tool: ToolBase, kwargs: dict[str, Any]) -> None:
        self.log.append(self.label)


class ExplodingApprover(Approver):
    """被调用即失败——用来证明某些工具根本没走审批。"""

    async def approve(self, tool: ToolBase, kwargs: dict[str, Any]) -> bool:
        raise AssertionError("这个工具不应该触发审批")


# --- Hook -------------------------------------------------------------------


def test_tool_hook_base_defines_before_and_after():
    """run_governed 调用的是 hook.before / hook.after，基类必须提供同名空实现。

    若基类把方法叫成别的名字（如 before_call），子类只覆盖基类方法就会静默失效。
    """
    hook = ToolHook()
    assert hook.before(ReadTool(), {}) is None
    assert hook.after(ReadTool(), {}, ToolResponse.succeed("x")) is None


async def test_hook_before_and_after_are_called():
    hook = RecordingHook()
    toolkit = Toolkit(tools=[ReadTool()], hooks=[hook])
    await toolkit.call_tool("read", text="hi")
    assert [kind for kind, _ in hook.events] == ["before", "after"]
    assert hook.events[0][1] == "read"


async def test_after_hook_receives_the_final_response():
    hook = RecordingHook()
    toolkit = Toolkit(tools=[ReadTool()], hooks=[hook])
    await toolkit.call_tool("read", text="hi")
    assert hook.events[1] == ("after", "hi")


async def test_before_hook_can_override_kwargs():
    tool = ReadTool()
    toolkit = Toolkit(tools=[tool], hooks=[RewritingHook()])
    resp = await toolkit.call_tool("read", text="original")
    assert tool.received == ["rewritten"]
    assert resp.get_text() == "rewritten"


async def test_multiple_hooks_before_run_in_registration_order():
    log: list[str] = []
    toolkit = Toolkit(
        tools=[ReadTool()],
        hooks=[OrderHook("first", log), OrderHook("second", log)],
    )
    await toolkit.call_tool("read", text="x")
    assert log == ["first", "second"]


# --- 人工确认 ---------------------------------------------------------------


async def test_read_only_tool_skips_approval():
    """只读工具免确认：approver 配了也不该被调用。"""
    toolkit = Toolkit(tools=[ReadTool()], approver=ExplodingApprover())
    resp = await toolkit.call_tool("read", text="hi")
    assert resp.status is ToolStatus.SUCCESS


async def test_write_tool_allowed_by_approver():
    toolkit = Toolkit(tools=[WriteTool()], approver=AutoApprover(True))
    resp = await toolkit.call_tool("write")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "written"


async def test_write_tool_denied_by_approver():
    tool = WriteTool()
    toolkit = Toolkit(tools=[tool], approver=AutoApprover(False))
    resp = await toolkit.call_tool("write")
    assert resp.status is ToolStatus.DENIED
    assert "拒绝" in resp.get_text()
    assert tool.calls == 0  # 被拒绝就不该执行


async def test_read_only_tool_with_forced_confirmation_is_still_asked():
    toolkit = Toolkit(tools=[AlwaysAskReadTool()], approver=AutoApprover(False))
    resp = await toolkit.call_tool("read_ask")
    assert resp.status is ToolStatus.DENIED


async def test_write_tool_with_confirmation_disabled_is_not_asked():
    toolkit = Toolkit(tools=[SilentWriteTool()], approver=ExplodingApprover())
    resp = await toolkit.call_tool("write_silent")
    assert resp.status is ToolStatus.SUCCESS


async def test_write_tool_without_approver_raises():
    toolkit = Toolkit(tools=[WriteTool()])
    with pytest.raises(RuntimeError, match="approver"):
        await toolkit.call_tool("write")


def test_needs_confirmation_tristate():
    write = WriteTool()
    assert needs_confirmation(write, {}) is True  # None + 非只读
    write.requires_confirmation = True
    assert needs_confirmation(write, {}) is True
    write.requires_confirmation = False
    assert needs_confirmation(write, {}) is False

    read = ReadTool()
    assert needs_confirmation(read, {}) is False  # None + 只读
    read.requires_confirmation = True
    assert needs_confirmation(read, {}) is True


async def test_auto_approver_returns_its_flag():
    assert await AutoApprover(True).approve(ReadTool(), {}) is True
    assert await AutoApprover(False).approve(ReadTool(), {}) is False


# --- 重试 -------------------------------------------------------------------


async def test_retry_succeeds_on_third_attempt():
    tool = FlakyTool(fail_times=2, max_retries=2)
    resp = await Toolkit(tools=[tool], approver=AutoApprover(True)).call_tool("flaky")
    assert resp.status is ToolStatus.SUCCESS
    assert resp.get_text() == "ok"
    assert tool.calls == 3


async def test_no_retry_by_default():
    tool = FlakyTool(fail_times=1)  # max_retries 默认 0
    resp = await Toolkit(tools=[tool], approver=AutoApprover(True)).call_tool("flaky")
    assert resp.status is ToolStatus.ERROR
    assert tool.calls == 1


async def test_retry_exhausted_returns_last_error():
    tool = FlakyTool(fail_times=99, max_retries=1)
    resp = await Toolkit(tools=[tool], approver=AutoApprover(True)).call_tool("flaky")
    assert resp.status is ToolStatus.ERROR
    assert tool.calls == 2


async def test_denied_result_is_not_retried():
    tool = DeniedTool(max_retries=2)
    resp = await Toolkit(tools=[tool]).call_tool("denied_tool")
    assert resp.status is ToolStatus.DENIED
    assert tool.calls == 1


async def test_backoff_is_exponential_between_retries(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("hello_agents.tool._governance.asyncio.sleep", fake_sleep)

    tool = FlakyTool(fail_times=2, max_retries=2, backoff=0.5)
    resp = await Toolkit(tools=[tool], approver=AutoApprover(True)).call_tool("flaky")
    assert resp.status is ToolStatus.SUCCESS
    assert slept == [0.5, 1.0]  # base * 2**attempt


# --- 协同 -------------------------------------------------------------------


async def test_full_governance_pipeline():
    """Hook 记录 + 审批放行 + 重试成功，且 after 只执行一次。"""
    hook = RecordingHook()
    tool = FlakyTool(fail_times=2, max_retries=2)
    toolkit = Toolkit(tools=[tool], hooks=[hook], approver=AutoApprover(True))

    resp = await toolkit.call_tool("flaky")

    assert resp.status is ToolStatus.SUCCESS
    assert tool.calls == 3  # 重试发生了
    assert [kind for kind, _ in hook.events] == ["before", "after"]  # 各只一次
    assert hook.events[1] == ("after", "ok")


async def test_after_hook_also_runs_on_denial():
    hook = RecordingHook()
    toolkit = Toolkit(tools=[WriteTool()], hooks=[hook], approver=AutoApprover(False))
    await toolkit.call_tool("write")
    assert [kind for kind, _ in hook.events] == ["before", "after"]
    assert "拒绝" in hook.events[1][1]
