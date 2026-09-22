#!/usr/bin/env python3
"""
SimpleAgent 入口脚本
"""

import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hello_agents.agents.simple_agent import SimpleAgent
from hello_agents.core.llm import HelloAgentsLLM
from hello_agents.tools.registry import ToolRegistry
from hello_agents.tools.builtin.search import SearchTool
from hello_agents.tools.builtin.calculator import CalculatorTool

if __name__ == '__main__':
    # 初始化工具注册表并注册工具
    tool_registry = ToolRegistry()
    tool_registry.register_tool('search', SearchTool())
    tool_registry.register_tool('calculator', CalculatorTool())

    # 初始化LLM客户端
    llm_client = HelloAgentsLLM()

    # 创建SimpleAgent实例
    agent = SimpleAgent(
        name="SimpleAgent",
        llm_client=llm_client,
        system_prompt="你是一个智能对话Agent，能够使用工具来回答问题。",
        tool_registry=tool_registry,
        enable_tool_calling=True,
        max_tool_iterations=3
    )

    # 运行Agent
    user_input = "请帮我搜索Python编程的最新趋势，并计算2+2的结果。"
    response = agent.run(user_input)
    print("最终响应:", response)
