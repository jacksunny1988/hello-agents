"""结构化输出：三模式（工具兜底 / 原生 json_schema / json_object）+ 校验修复闭环。

M7 的核心矛盾是「服务端保证的强度不一样」：`json_object` 只保证是合法 JSON，
`json_schema` 严格但看模型脸色，工具兜底最通用但走的是工具语义。三条路都收口到
同一个客户端闭环：**取值 → Pydantic 校验 → 失败就把精简错误回灌让模型自己修**，
上限 `max_fix_rounds` 轮。这样即使选最弱的模式，也有强保证兜底。

只支持非流式：流式增量 JSON 的解析是另一个课题，传流式模型进来会直接报错，
不静默走错路径。
"""

import copy
import json
import re
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ValidationError

from ._base import ChatModelBase
from ._formatter import (
    to_response_format_json_object,
    to_response_format_json_schema,
)
from ._response import ChatResponse, FinishedReason
from ._tool import Tool
from ._usage import ChatUsage
from .message import Message, Role, TextBlock, ToolResultBlock

StructuredMode = Literal["tool", "json_schema", "json_object"]

_MODES: tuple[StructuredMode, ...] = ("tool", "json_schema", "json_object")

# 工具兜底模式里那个「提交结果」工具的名字，也是提示词与断言共用的常量。
SUBMIT_TOOL_NAME = "submit_result"

# 决策 2：不用 tool_choice="required"（DeepSeek 思考模式会 400），靠强提示逼进工具调用。
_TOOL_SYSTEM_PROMPT = (
    f"你必须调用 {SUBMIT_TOOL_NAME} 工具来提交最终结果，不得用正文回答。"
    "把结果放进该工具的参数里，正文留空。"
)

# 回灌给模型的错误文本上限：列太多条既烧 token 又淹没有效信息。
_MAX_ERROR_ITEMS = 10
_MAX_ERROR_CHARS = 1200

# strict 模式要求「对象节点必须禁掉额外字段、且所有属性都进 required」，
# 这两条由 `_strictify` 强制写入；下面这些键则一律删掉。
_STRICT_STRIPPED_KEYS = ("title", "default")
# 子 schema 可能挂在这几个键下面，递归时都要走一遍。
_SCHEMA_BRANCH_KEYS = ("anyOf", "oneOf", "allOf")


class StructuredOutputError(Exception):
    """修复轮数耗尽仍未得到合法结果。"""


class NativeJsonSchemaUnsupportedError(StructuredOutputError):
    """注册表能力位为 False 时请求 json_schema 模式。

    继承 `StructuredOutputError`：调用方只关心「结构化没成功」时一个 `except` 就够。
    """


class StructuredAttempt(BaseModel):
    """一次请求的结果：失败时带上回灌给模型的精简错误。"""

    index: int
    error: str | None = None
    usage: ChatUsage | None = None


class StructuredStats(BaseModel):
    """整次结构化调用的统计，供 demo 横向对比三种模式。"""

    mode: StructuredMode
    attempts: list[StructuredAttempt]

    @property
    def rounds(self) -> int:
        """修复轮数：成功那次不算修复。"""
        return max(0, len(self.attempts) - 1)

    @property
    def ok_first_try(self) -> bool:
        return len(self.attempts) == 1

    @property
    def usage(self) -> ChatUsage:
        """各轮合计。厂商没给 usage 的轮按 0 计。"""
        total = ChatUsage()
        for attempt in self.attempts:
            if attempt.usage is None:
                continue
            total.input_tokens += attempt.usage.input_tokens
            total.output_tokens += attempt.usage.output_tokens
            total.cache_read_tokens += attempt.usage.cache_read_tokens
            total.time += attempt.usage.time
        return total


class _SubmitResultTool(Tool):
    """把「最终结构」伪装成一个必须调用的工具（规格 §4）。

    `run` 只是满足 ABC 的最小实现，**`extract_structured` 全程不会调用它**——
    结构化输出的取值路径是「解析 `ToolCallBlock.arguments`」，不是「执行工具」。
    """

    name = SUBMIT_TOOL_NAME
    description = "提交最终的结构化结果。这是唯一被接受的输出方式。"
    parameters: ClassVar[dict[str, Any]] = {}  # 占位；__init__ 用真实 schema 覆盖

    def __init__(self, schema: dict[str, Any]) -> None:
        self.parameters = schema

    async def run(self, arguments: dict[str, Any]) -> object:
        return arguments


def strict_json_schema(output_model: type[BaseModel]) -> dict[str, Any]:
    """把 Pydantic 生成的 schema 转成 OpenAI strict 模式能接受的形状。

    改动两处、删除两键，递归到所有子 schema：

    - `additionalProperties = False`（strict 硬要求，`extra="allow"` 的模型因此
      不能走这个模式）；
    - `required` 覆盖为**全部**属性名；
    - 删 `title`（Pydantic 到处生成的噪音）与 `default`（字段已必填，
      「必填且有默认值」自相矛盾，部分严格校验器直接拒绝）。

    **语义影响**：strict 模式下没有「可省略」——带默认值的字段也会进 `required`。
    想让某字段可以不填，用 `X | None = None`（结果是 required 但允许 `null`）。

    `$defs` / `$ref` 保留不展开：标准 JSON Schema，OpenAI strict 支持，而内联展开
    要处理递归引用，代价远大于收益。嵌套模型的定义同样会被递归改写。
    """
    schema = copy.deepcopy(output_model.model_json_schema())
    _strictify(schema)
    return schema


def _strictify(node: Any) -> None:
    """就地改写一个 schema 节点，并递归它的所有子节点。"""
    if not isinstance(node, dict):
        return

    properties = node.get("properties")
    if isinstance(properties, dict):
        for value in properties.values():
            _strictify(value)
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())

    for key in ("$defs", "definitions"):
        definitions = node.get(key)
        if isinstance(definitions, dict):
            for definition in definitions.values():
                _strictify(definition)

    items = node.get("items")
    if isinstance(items, list):
        for item in items:
            _strictify(item)
    else:
        _strictify(items)

    for key in _SCHEMA_BRANCH_KEYS:
        branches = node.get(key)
        if isinstance(branches, list):
            for branch in branches:
                _strictify(branch)

    for key in _STRICT_STRIPPED_KEYS:
        node.pop(key, None)


async def extract_structured[T: BaseModel](
    model: ChatModelBase,
    messages: list[Message],
    output_model: type[T],
    *,
    mode: StructuredMode = "tool",
    max_fix_rounds: int = 2,
) -> T:
    """按 `mode` 取一次结构化结果，失败则回灌错误让模型自修（最多 `max_fix_rounds` 轮）。

    只需要强类型返回值时用这个；要「修复了几轮 / 花了多少 token」用
    `extract_structured_with_stats`。两者共用同一个循环。
    """
    value, _ = await extract_structured_with_stats(
        model, messages, output_model, mode=mode, max_fix_rounds=max_fix_rounds
    )
    return value


async def extract_structured_with_stats[T: BaseModel](
    model: ChatModelBase,
    messages: list[Message],
    output_model: type[T],
    *,
    mode: StructuredMode = "tool",
    max_fix_rounds: int = 2,
) -> tuple[T, StructuredStats]:
    """同 `extract_structured`，另外返回统计。

    轮数语义：`max_fix_rounds=N` 最多发 **N+1** 次请求（1 次原始 + N 次修复）；
    负数按 0 处理，即只发一次。
    """
    _validate_entry(model, output_model, mode)

    # 调用方的列表一个字节都不动：demo 会拿同一个列表跑三种模式。
    base = list(messages)
    current = _initial_messages(base, output_model, mode)
    attempts: list[StructuredAttempt] = []
    budget = max(0, max_fix_rounds)  # 负数退化成「不修复」，同 `_with_retry`
    last_error: ValidationError | None = None

    for round_index in range(budget + 1):
        response = await model(current, **_request_kwargs(output_model, mode))
        payload, error = _extract_payload(response, mode)

        if payload is not None:
            try:
                value = output_model.model_validate(payload)
            except ValidationError as exc:
                last_error = exc
                error = _validation_error_text(exc)
            else:
                attempts.append(
                    StructuredAttempt(index=round_index, usage=response.usage)
                )
                return value, StructuredStats(mode=mode, attempts=attempts)

        # 截断提示对「解析失败」与「校验失败」两条路都要加：被砍断的 JSON 最常见的
        # 症状就是解析失败，只在校验失败时提示会漏掉最主要的那种情况。
        error = _with_truncation_hint(error, response)
        attempts.append(
            StructuredAttempt(index=round_index, error=error, usage=response.usage)
        )
        if round_index < budget:
            current = current + _repair_messages(response, error, mode)

    message = _exhausted_message(mode, attempts)
    if last_error is not None:
        # 保留异常链：排查时能顺着 __cause__ 看到最后一次的 ValidationError。
        raise StructuredOutputError(message) from last_error
    raise StructuredOutputError(message)


def _validate_entry(
    model: ChatModelBase, output_model: type[BaseModel], mode: str
) -> None:
    """发请求之前把三种用法错误挡掉，别让它们变成看不懂的报错。"""
    if mode not in _MODES:
        raise ValueError(f"未知 mode {mode!r}，可选：{'、'.join(_MODES)}")
    if not (isinstance(output_model, type) and issubclass(output_model, BaseModel)):
        raise TypeError(f"output_model 必须是 BaseModel 子类，收到 {output_model!r}")
    if model.stream:
        raise ValueError("extract_structured 只支持非流式模型（stream=False）")
    if mode == "json_schema" and not model.config.supports_native_json_schema:
        # 决策 3：不静默降级——「以为走了严格模式、其实退化成弱保证」是最坏的结果。
        raise NativeJsonSchemaUnsupportedError(
            f"provider={model.config.provider.value} 的注册表能力位 "
            f"supports_native_json_schema=False，尚未实测支持原生 strict json_schema。"
            f'请改用 mode="tool"，或先跑 examples/model_m7_probe.py 实测后回填注册表。'
        )


def _initial_messages(
    base: list[Message], output_model: type[BaseModel], mode: StructuredMode
) -> list[Message]:
    """按模式在原始消息前面插一条系统消息。

    插在最前而不是合并进调用方的 system 消息：不改写调用方内容，同时让
    「必须调工具 / 必须是 JSON」这条指令先于其它指令出现。
    """
    if mode == "tool":
        return [Message.system(_TOOL_SYSTEM_PROMPT), *base]
    if mode == "json_object":
        # 服务端只保证「是合法 JSON」，字段约束只能靠提示词说清楚。
        return [Message.system(_json_object_prompt(output_model)), *base]
    # json_schema 的约束由服务端承担，消息原样发出。
    return base


def _json_object_prompt(output_model: type[BaseModel]) -> str:
    """内联**原样** schema：保留 default 与「哪些字段可选」，比 strict 形状更有用。"""
    schema = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
    return (
        "只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。"
        "JSON 必须符合下面的 JSON Schema：\n" + schema
    )


def _request_kwargs(output_model: type[BaseModel], mode: StructuredMode) -> dict:
    if mode == "tool":
        # 工具参数用原样 schema，不做 strict 化（strict 只服务 json_schema 模式）。
        return {
            "tools": [_SubmitResultTool(output_model.model_json_schema())],
            "tool_choice": "auto",
        }
    if mode == "json_schema":
        return {
            "response_format": to_response_format_json_schema(
                _schema_name(output_model), strict_json_schema(output_model)
            )
        }
    return {"response_format": to_response_format_json_object()}


def _schema_name(output_model: type[BaseModel]) -> str:
    """`json_schema.name`：厂商要求 `[A-Za-z0-9_-]{1,64}`。

    用类名而不是常量，是为了在厂商后台的日志里能一眼看出是哪个模型。
    """
    return re.sub(r"[^A-Za-z0-9_-]", "_", output_model.__name__)[:64] or "output"


def _extract_payload(
    response: ChatResponse, mode: StructuredMode
) -> tuple[Any, str | None]:
    """从响应里取出待校验的 payload。返回 `(payload, 错误文本)`，二者恰有一个非空。"""
    if mode == "tool":
        return _tool_payload(response)
    return _text_payload(response)


def _tool_payload(response: ChatResponse) -> tuple[Any, str | None]:
    calls = [
        call for call in response.get_tool_calls() if call.name == SUBMIT_TOOL_NAME
    ]
    if not calls:
        return None, (
            f"没有调用 {SUBMIT_TOOL_NAME} 工具。必须以工具调用的形式提交结果。"
        )

    try:
        arguments = json.loads(calls[0].arguments)
    except json.JSONDecodeError as exc:
        return None, f"{SUBMIT_TOOL_NAME} 的参数不是合法 JSON：{exc}"
    except RecursionError:
        # 同 M6 的 `_invoke`：超深嵌套会让 json.loads 撞上解释器的递归上限。
        return None, f"{SUBMIT_TOOL_NAME} 的参数嵌套层级过深，无法解析"

    if not isinstance(arguments, dict):
        return None, (
            f"{SUBMIT_TOOL_NAME} 的参数必须是 JSON 对象，"
            f"收到 {type(arguments).__name__}"
        )
    return arguments, None


def _text_payload(response: ChatResponse) -> tuple[Any, str | None]:
    """正文模式的取值：拼正文 → 容错解析 JSON。"""
    text = "".join(
        block.text for block in response.content if isinstance(block, TextBlock)
    )
    if not text.strip():
        return None, "响应里没有正文，无法解析结构化结果。"

    try:
        return _parse_json_payload(text), None
    except json.JSONDecodeError as exc:
        return None, f"正文不是合法 JSON：{exc}"
    except RecursionError:
        return None, "正文里的 JSON 嵌套层级过深，无法解析"


def _parse_json_payload(text: str) -> Any:
    """解析正文里的 JSON：先原样，再尝试剥掉 Markdown 代码围栏。

    容错不削弱保证——真正的门是后面的 `model_validate`；这里只是省掉一轮
    「模型手滑加了围栏」的修复成本。返回值标成 `Any`：解析出来不是 dict 时
    不在这里拦，统一交给 `model_validate` 报错（一个报错出口比两个好）。
    """
    stripped = text.strip()
    candidates = [stripped]
    fenced = _strip_code_fence(stripped)
    if fenced is not None:
        candidates.append(fenced)

    for candidate in candidates[:-1]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return json.loads(candidates[-1])  # 最后一次让它抛，错误信息才是真的


_FENCE_RE = re.compile(
    r"^\s*```[A-Za-z0-9_-]*\s*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL
)


def _strip_code_fence(text: str) -> str | None:
    match = _FENCE_RE.match(text)
    return match.group("body") if match else None


def _repair_messages(
    response: ChatResponse, error: str, mode: StructuredMode
) -> list[Message]:
    """把「上一轮错在哪」拼成下一轮要追加的消息。

    三种形状，按协议约束选：

    - tool 模式有工具调用：assistant(原始 tool_calls) + 一条 role=tool 的结果，
      每个调用都要有配对结果，漏一个请求本身就不合法；
    - tool 模式没调工具：没有 `tool_call_id` 可配对，退回 user 消息；
    - 正文模式：assistant(原始输出) + user(错误)，但响应里没正文时只发 user
      ——空 content 的 assistant 消息在部分端点是非法请求。
    """
    if mode == "tool":
        calls = response.get_tool_calls()
        if calls:
            results = [
                ToolResultBlock(
                    tool_call_id=call.id,
                    # 幻觉调用的其它工具也要回灌，否则 assistant 的 tool_calls
                    # 会缺配对；内容用 M6 那套「未知工具」文案。
                    output=(
                        error
                        if call.name == SUBMIT_TOOL_NAME
                        else f"未知工具 {call.name!r}"
                    ),
                    is_error=True,
                    name=call.name or None,
                )
                for call in calls
            ]
            return [response.to_message(), Message(role=Role.TOOL, content=results)]
        return [response.to_message(), Message.user(error)]

    if not response.content:
        return [Message.user(error)]
    return [response.to_message(), Message.user(error)]


def _validation_error_text(exc: ValidationError) -> str:
    """`ValidationError` → 给模型看的精简文本。

    刻意不用 `str(exc)`：pydantic 默认输出里带 `input`，会把模型刚输出的一长串
    原文再喂回去，白烧 token。这里只取 `loc` / `msg` / `type`，并加上限。
    """
    errors = exc.errors()
    lines = [
        f"- {_format_location(item.get('loc', ()))}: {item.get('msg')} "
        f"[{item.get('type')}]"
        for item in errors[:_MAX_ERROR_ITEMS]
    ]
    if len(errors) > _MAX_ERROR_ITEMS:
        lines.append(f"…（其余 {len(errors) - _MAX_ERROR_ITEMS} 条略）")

    text = (
        "上次提交未通过校验，请修正后重新提交：\n"
        + "\n".join(lines)
        + "\n只提交修正后的完整结果，不要解释。"
    )
    return _truncate(text, _MAX_ERROR_CHARS)


def _with_truncation_hint(error: str, response: ChatResponse) -> str:
    """截断的响应在正文模式里表现为「JSON 解析失败」，不点破会把模型带偏：
    它以为是格式写错了，其实是被砍了。"""
    if response.finished_reason is not FinishedReason.LENGTH:
        return error
    return (
        "上一轮输出因长度上限被截断（finish_reason=length），"
        "请输出更精简的完整 JSON。\n" + error
    )


def _format_location(loc: tuple) -> str:
    return ".".join(str(part) for part in loc) or "(根)"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def _exhausted_message(mode: StructuredMode, attempts: list[StructuredAttempt]) -> str:
    last = attempts[-1].error
    return (
        f"结构化输出失败（mode={mode}）：{len(attempts)} 次尝试都未通过校验。"
        f"最后一次错误：{last}"
    )
