"""
异常体系 - 定义框架中使用的异常类
"""


class AgentError(Exception):
    """Agent相关异常的基类"""



class LLMError(AgentError):
    """LLM调用相关异常"""



class ToolError(AgentError):
    """工具执行相关异常"""



class ConfigError(AgentError):
    """配置相关异常"""



class MaxStepsError(AgentError):
    """达到最大步数限制异常"""



class ParsingError(AgentError):
    """解析响应异常"""

