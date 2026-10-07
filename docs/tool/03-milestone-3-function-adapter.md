# 里程碑 T3：普通函数自动适配（类型注解 + docstring → JSON Schema）

> 目标：把一个普通 Python 函数（`def` / `async def`）包装成 `ToolBase`，
> 自动从**签名/类型注解**生成入参 JSON Schema、从 **docstring** 提取函数与参数说明，
> 并把返回值归一为 `ToolResponse`。
> 对标（只读）：`agentscope/tool/_adapters.py` 的 `FunctionTool`、`agentscope/tool/_utils.py`。

---

## 3.1 设计原理（先理解）

### 1) 为什么需要"函数适配器"

`ToolBase` 要求工具是一个类、还要手写 `input_schema`。但大量现成能力就是普通函数，
为每个函数写一个类 + 手写 schema 既啰嗦又容易和真实签名不一致。

`FunctionTool` 就是**适配器模式**：运行时反射函数签名，自动生成 schema，让普通函数
立刻具备 ToolBase 的一切能力（执行收口、超时、后续的 hook/确认）。

### 2) JSON Schema 从哪来——为什么用 pydantic 中转

手写"Python 类型 → JSON Schema"映射要处理 int/str/float/bool/list/dict/Optional/
嵌套模型/枚举……非常繁琐。而 pydantic 已经会做这件事：

- `pydantic.create_model(名字, **fields)` 可在运行时动态造一个 BaseModel；
- 每个 field 用 `(类型, Field(default=..., description=...))` 声明；
- `model.model_json_schema()` 直接产出标准 JSON Schema。

我们只需把函数参数翻译成 pydantic fields，复用你在 model 阶段已学的 pydantic 能力。

### 3) docstring 提供"人话描述"

类型注解解决"类型"，但给模型看的**自然语言说明**在 docstring 里。用第三方库
`docstring_parser` 解析（支持 Google / reST / NumPy / Epytext 风格）：

- 函数整体描述 = `short_description` + `long_description`；
- 每个参数描述 = `params` 里同名参数的 `description`。

推荐 **Google 风格**（最常见）：

```python
def add(a: int, b: int = 2) -> int:
    """两数相加。

    这是更长的说明，会拼在短描述之后。

    Args:
        a: 第一个加数。
        b: 第二个加数，有默认值、非必填。
    """
    return a + b
```

### 4) 为什么要去掉 title

pydantic 导出的 schema 会给模型本身和每个 object/array 属性自动加 `"title": ...`，
这些是对模型无意义的噪音、还浪费 token。`_remove_title_field` 递归删除顶层、
properties、items、additionalProperties、`$defs` 里的 title。

### 5) 返回值为什么要归一

普通函数可能返回 str / int / dict / list / 自定义对象，而框架统一需要 ToolResponse：
- 已是 ToolResponse → 原样返回；
- str → 直接作为文本；
- 其它 → 优先 `json.dumps(..., ensure_ascii=False)`；不可序列化再退回 `str(result)`。

---

## 3.2 依赖安装

```powershell
Set-Location D:\projects\hello-agents
uv add docstring-parser
```

> 导入名是 `docstring_parser`（包名带连字符、导入用下划线）。

---

## 3.3 接口契约：填充 `_adapters.py`

### 成员说明

| 成员 | 种类 | 职责 |
|---|---|---|
| `_remove_title_field` | 函数 | 递归删除 schema 中的 title 噪音 |
| `_extract_func_description` | 函数 | docstring → 函数整体描述 |
| `_extract_input_schema` | 函数 | 签名 + 注解 + docstring → 入参 JSON Schema |
| `FunctionTool` | ToolBase 子类 | 包装普通函数；call 内执行函数并归一结果 |
| `_normalize_result` | 函数 | 任意返回值 → ToolResponse |

### 完整代码

```python
import asyncio
import inspect
import json
from typing import Any, Callable

from docstring_parser import parse
from pydantic import ConfigDict, Field, create_model

from ._base import ToolBase
from ._response import ToolResponse


def _remove_title_field(schema: dict) -> dict:
    """递归删除 pydantic 自动生成的 title 噪音。"""
    if "title" in schema:
        schema.pop("title")
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _remove_title_field(prop)
    if "items" in schema and isinstance(schema["items"], dict):
        _remove_title_field(schema["items"])
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict
    ):
        _remove_title_field(schema["additionalProperties"])
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for sub_schema in schema["$defs"].values():
            if isinstance(sub_schema, dict):
                _remove_title_field(sub_schema)
    return schema


def _extract_func_description(docstring: str) -> str:
    """docstring → short + long 描述。"""
    parsed = parse(docstring or "")
    parts: list[str] = []
    if parsed.short_description is not None:
        parts.append(parsed.short_description)
    if parsed.long_description is not None:
        parts.append(parsed.long_description)
    return "\n".join(parts)


def _extract_input_schema(func: Callable) -> dict:
    """从函数签名/注解/docstring 生成入参 JSON Schema。"""
    parsed = parse(func.__doc__ or "")
    param_docs = {param.arg_name: param.description for param in parsed.params}

    fields: dict[str, Any] = {}
    for param_name, sig_param in inspect.signature(func).parameters.items():
        # 跳过 self/cls；*args/**kwargs 不纳入（见 3.5 边界）
        if param_name in ("self", "cls"):
            continue
        if sig_param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        annotation = (
            Any
            if sig_param.annotation is inspect.Parameter.empty
            else sig_param.annotation
        )
        has_default = sig_param.default is not inspect.Parameter.empty
        field = Field(
            description=param_docs.get(param_name),
            default=... if not has_default else sig_param.default,
        )
        fields[param_name] = (annotation, field)

    dynamic_model = create_model(
        "_DynamicToolParams",
        __config__=ConfigDict(arbitrary_types_allowed=True),
        **fields,
    )
    schema = dynamic_model.model_json_schema()
    return _remove_title_field(schema)


def _normalize_result(result: Any) -> ToolResponse:
    """任意函数返回值 → ToolResponse。"""
    if isinstance(result, ToolResponse):
        return result
    if isinstance(result, str):
        return ToolResponse.succeed(result)
    try:
        return ToolResponse.succeed(json.dumps(result, ensure_ascii=False))
    except (TypeError, ValueError):
        return ToolResponse.succeed(str(result))


class FunctionTool(ToolBase):
    """把普通 Python 函数适配为 ToolBase。"""

    def __init__(
        self,
        func: Callable,
        *,
        name: str | None = None,
        description: str | None = None,
        is_read_only: bool = False,
        is_concurrency_safe: bool = True,
    ) -> None:
        # 先准备元数据与 schema，再调用 super().__init__（其中会校验 schema）
        self._func = func
        self.name = name or func.__name__
        self.description = description or _extract_func_description(
            func.__doc__ or ""
        )
        self.input_schema = _extract_input_schema(func)
        self.is_read_only = is_read_only
        self.is_concurrency_safe = is_concurrency_safe
        super().__init__()

    async def call(self, **kwargs: Any) -> ToolResponse:
        """执行被包装函数：async 直接 await，sync 丢线程池（不阻塞事件循环）。"""
        if inspect.iscoroutinefunction(self._func):
            result = await self._func(**kwargs)
        else:
            result = await asyncio.to_thread(self._func, **kwargs)
        return _normalize_result(result)
```

> 说明：`FunctionTool.call` 本身恒为 `async def`（走 T2 的 await 分支，不会被二次
> 丢线程池）；它在内部显式对同步 `func` 使用 `to_thread`，保证不阻塞事件循环。

---

## 3.4 留给你的动手任务

### 1) `uv add docstring-parser`，在 `_adapters.py` 填入上述实现。

### 2) 写演示 `examples/tool_t3_function.py`：

```python
import asyncio

from hello_agents.tool._adapters import FunctionTool


def add(a: int, b: int = 2) -> int:
    """两数相加。

    Args:
        a: 第一个加数。
        b: 第二个加数，默认 2、非必填。
    """
    return a + b


async def greet(name: str) -> str:
    """向某人打招呼。

    Args:
        name: 要问候的名字。
    """
    return f"hello {name}"


def list_user() -> dict:
    """返回一个字典，验证返回值 JSON 归一。"""
    return {"id": 1, "tags": ["x", "y"]}


async def main() -> None:
    add_tool = FunctionTool(add, is_read_only=True)
    print(add_tool.name)
    print(add_tool.description)
    print(add_tool.input_schema)
    print((await add_tool(a=1)).get_text())       # 3（b 用默认值）
    print((await add_tool(a=1, b=5)).get_text())  # 6

    greet_tool = FunctionTool(greet)
    print((await greet_tool(name="world")).get_text())

    user_tool = FunctionTool(list_user)
    print((await user_tool()).get_text())         # JSON 字符串


asyncio.run(main())
```

观察点：
- `input_schema` 中 `a` 在 required、`b` 因有默认值不在 required；
- int 返回被归一为 `"3"`；dict 返回被 `json.dumps` 成 JSON 字符串。

### 3) 写测试 `tests/test_tool_t3.py`（离线、确定性）：

- **schema 类型映射**：`add` 的 a/b 属性类型为 `"integer"`；
- **required**：无默认值参数在 required、有默认值的不在；
- **参数描述**：docstring 中 `a` 的描述进入 properties.a.description；
- **函数描述**：name/description 正确，long_description 被拼接；
- **同步函数调用**：返回 SUCCESS、结果归一正确（int→"3"）；默认值参数可省略；
- **异步函数调用**：greet 返回 SUCCESS、文本正确；
- **dict/list 返回**：被 JSON 序列化；
- **返回 ToolResponse 的函数**：原样返回、不二次包装；
- **显式覆盖**：传 name=/description= 时优先生效；
- **无注解参数**：类型为 Any（对应 property 无 type 约束），不报错；
- **无 docstring**：description 为 ""，不报错；
- **无参函数**：properties 为 {}、可实例化与调用；
- ***args / ****kwargs 被跳过**：定义 `def f(*args, **kwargs)`，schema properties 为空；
- **同步函数仍走线程池（T2 不回归）**：函数内 `import threading` 返回 ident，
  断言与主线程不同。

---

## 3.5 边界与裁剪说明

- **`*args` / `**kwargs` 默认不纳入 schema**：工具入参应是可具名、可被模型填写的
  字段；可变参数在 JSON Schema 里无法良好表达。agentscope 提供
  `include_var_positional/include_var_keyword` 开关，我们直接裁剪（思考题：若要支持
  `**kwargs`，可建模为 `additionalProperties`）。
- **generator 函数不支持**：我们工具一次返回一个完整结果，不做工具流式。
- **无注解** → `Any`（schema property 无 type，等于接受任意类型）；
- **`Optional[X]` / `X | None` 且默认 None**：pydantic 判为非必填、类型含 null；
- **自定义类型**：`create_model` 配置了 `arbitrary_types_allowed=True`，但任意类型
  导出的 schema 可能不完整——工具参数应尽量用基础类型 / pydantic 模型 / TypedDict。

---

## 3.6 验收清单

- [ ] docstring-parser 已加入依赖；
- [ ] `_adapters.py` 与契约一致（三个提取函数 + FunctionTool + _normalize_result）；
- [ ] tool_t3 演示输出符合观察点；
- [ ] test_tool_t3 全绿（含 *args/**kwargs 跳过、无注解、无 docstring、线程池不回归）；
- [ ] T1/T2 回归全绿；
- [ ] ruff 干净：

```powershell
uv run ruff check hello_agents/tool examples/tool_t3_function.py tests/test_tool_t3.py
uv run ruff format --check hello_agents/tool examples/tool_t3_function.py tests/test_tool_t3.py
uv run pytest tests/test_tool_t1.py tests/test_tool_t2.py tests/test_tool_t3.py -q
```

---

## 3.7 agentscope 源码对照

| 你的实现 | agentscope | 对照/裁剪 |
|---|---|---|
| FunctionTool.__init__ | `_adapters.py:49-88` | 元数据/schema 提取对应 |
| FunctionTool.call | `_adapters.py:109-146` | 我们同步分支用 to_thread；不做 generator 流式 |
| _normalize_result | `_adapters.py:148-164` _convert_func_result_to_chunk | 语义对应，产出 ToolResponse 而非 ToolChunk |
| _extract_input_schema | `_utils.py:68-160` | 逻辑对应；裁剪 var-positional/keyword 开关 |
| _extract_func_description | `_utils.py:46-65` | 一致 |
| _remove_title_field | `_utils.py:10-43` | 一致 |

---

## 完成后

把 `_adapters.py`、tool_t3 演示输出与 pytest 结果贴给我 review。
通过后进入 **T4：Toolkit / ToolGroup（注册、查找、分组、动态增删）**。
