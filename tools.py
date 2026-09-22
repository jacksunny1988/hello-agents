import sys
from serpapi import Client
from dotenv import load_dotenv
from typing import Dict, Any
import os

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()


def search(query, api_key=None):
    """
    一个基于SerpApi的实战网页搜索引擎工具。
    它会智能地解析搜索结果，优先返回直接答案或知识图谱信息。
    Args:
        query (str): 搜索查询字符串。
       """
    
    print(f"🔍 正在执行 [SerpApi] 网页搜索: {query}")
    try:
        api_key = os.getenv("SERPAPI_API_KEY")
        if not api_key:
            return "错误:SERPAPI_API_KEY 未在 .env 文件中配置。"

        params = {
            "engine": "google",
            "q": query,
            "api_key": api_key,
            "gl": "cn",  # 国家代码
            "hl": "zh-cn", # 语言代码
        }
        
        client = Client(api_key=api_key)
        results = client.search(params)
        
        # 智能解析:优先寻找最直接的答案
        if "answer_box_list" in results:
            return "\n".join(results["answer_box_list"])
        if "answer_box" in results and "answer" in results["answer_box"]:
            return results["answer_box"]["answer"]
        if "knowledge_graph" in results and "description" in results["knowledge_graph"]:
            return results["knowledge_graph"]["description"]
        if "organic_results" in results and results["organic_results"]:
            # 如果没有直接答案，则返回前三个有机结果的摘要
            snippets = [
                f"[{i+1}] {res.get('title', '')}\n{res.get('snippet', '')}"
                for i, res in enumerate(results["organic_results"][:3])
            ]
            return "\n\n".join(snippets)
        
        return f"对不起，没有找到关于 '{query}' 的信息。"

    except Exception as e:
        return f"搜索时发生错误: {e}"

class ToolExecutor:
    """
    一个工具执行器，负责管理和执行工具。
    """
    def __init__(self):
         self.tools: Dict[str, Dict[str, Any]] = {}

    def registerTool(self, name: str, description: str, func: callable):
        """
        注册一个工具。
        Args:
            name (str): 工具名称。
            description (str): 工具描述。
            func (callable): 工具的执行函数。
        """
        if name in self.tools:
            raise ValueError(f"工具 '{name}' 已经注册。")
        self.tools[name] = {
            "description": description,
            "func": func
        }

    def get_tool(self, name: str) -> callable:
        """
        获取已注册的工具信息。
        Args:
            name (str): 工具名称。
        Returns:
            Dict[str, Any]: 工具信息，包括描述和执行函数。
        """
        if name not in self.tools:
            raise ValueError(f"工具 '{name}' 未注册。")
        return self.tools.get(name, {}).get("func")

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """
        执行指定的工具。
        Args:
            tool_name (str): 工具名称。
            *args: 传递给工具的参数。
            **kwargs: 传递给工具的关键字参数。
        Returns:
            Any: 工具执行的结果。
        """
        if tool_name not in self.tools:
            raise ValueError(f"工具 '{tool_name}' 未注册。")
        return self.tools[tool_name](*args, **kwargs)

    def getAvailableTools(self) -> str:
        """
        获取所有可用工具的格式化描述字符串。
        """
        return "\n".join([
            f"- {name}: {info['description']}" 
            for name, info in self.tools.items()
        ])

# --- 工具初始化与使用示例 ---
if __name__ == '__main__':


    # 1. 初始化工具执行器
    toolExecutor = ToolExecutor()

    # 2. 注册我们的实战搜索工具
    search_description = "一个网页搜索引擎。当你需要回答关于时事、事实以及在你的知识库中找不到的信息时，应使用此工具。"
    toolExecutor.registerTool("Search", search_description, search)
    
    # 3. 打印可用的工具
    print("\n--- 可用的工具 ---")
    print(toolExecutor.getAvailableTools())

    # 4. 智能体的Action调用，这次我们问一个实时性的问题
    print("\n--- 执行 Action: Search['英伟达最新的GPU型号是什么'] ---")
    tool_name = "Search"
    tool_input = "英伟达最新的GPU型号是什么"

    tool_function = toolExecutor.get_tool(tool_name)
    if tool_function:
        observation = tool_function(tool_input)
        print("--- 观察 (Observation) ---")
        print(observation)
    else:
        print(f"错误:未找到名为 '{tool_name}' 的工具。")