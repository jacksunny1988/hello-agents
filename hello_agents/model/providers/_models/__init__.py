"""模型卡片数据目录（一模型一个 YAML）。

这里没有 Python 代码，只有 `*.yaml`。`__init__.py` 的存在是为了让它成为一个
**包**，从而能用 `importlib.resources.files("...providers._models")` 按包内资源
读取——安装成 wheel 后依然可访问，不依赖「当前工作目录」。
"""
