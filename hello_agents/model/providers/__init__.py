"""三家共用的 OpenAI 兼容接入层。

DashScope / DeepSeek / 智谱 GLM 在兼容模式下共用同一个 `openai` SDK，
只切 `base_url` / `api_key` / `model`，所以 M3 用一个参数化的
`OpenAICompatModel` 就足以表达三家——差异全在 `ModelConfig` 的能力位里。

等某家出现独有能力时（M7/M8）再分化出 `dashscope.py` / `deepseek.py` /
`zhipu.py`，届时只需各写一个 `_call_api`，基类的模板方法完全复用。

典型用法：
    from hello_agents.model.providers import build_model

    model = build_model("deepseek:deepseek-flash")
    response = await model([Message.user("你好")])
"""

from ._openai_compat import OpenAICompatModel, build_model

__all__ = [
    "OpenAICompatModel",
    "build_model",
]
