"""
搜索工具 - 提供网页搜索功能
"""

import os
from typing import Any, Optional
from ..base import BaseTool


class SearchTool(BaseTool):
    """
    搜索工具，基于SerpApi提供网页搜索功能。

    需要在环境变量中设置 SERPAPI_API_KEY。
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化搜索工具。

        Args:
            api_key: SerpApi API密钥，默认从环境变量读取
        """
        super().__init__(
            name="search",
            description="一个网页搜索引擎。当你需要回答关于时事、事实以及在你的知识库中找不到的信息时，应使用此工具。"
        )
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")

    def run(self, query: str, **kwargs) -> str:
        """
        执行网页搜索。

        Args:
            query: 搜索查询字符串
            **kwargs: 额外参数

        Returns:
            搜索结果字符串
        """
        if not self.api_key:
            return "错误: SERPAPI_API_KEY 未配置。请在 .env 文件中设置。"

        try:
            return self._search_with_serpapi(query)
        except Exception as e:
            return f"搜索时发生错误: {str(e)}"

    def _search_with_serpapi(self, query: str) -> str:
        """
        使用SerpApi执行搜索。

        Args:
            query: 搜索查询

        Returns:
            搜索结果
        """
        try:
            from serpapi import Client
        except ImportError:
            return "错误: 请安装 serpapi 包。运行: pip install google-search-results"

        client = Client(api_key=self.api_key)
        params = {
            "engine": "google",
            "q": query,
            "gl": "cn",
            "hl": "zh-cn",
            "num": 5,
        }

        results = client.search(params)

        # 智能解析搜索结果
        if "answer_box_list" in results:
            return "\n".join(results["answer_box_list"])
        if "answer_box" in results and "answer" in results["answer_box"]:
            return results["answer_box"]["answer"]
        if "knowledge_graph" in results and "description" in results["knowledge_graph"]:
            return results["knowledge_graph"]["description"]
        if "organic_results" in results and results["organic_results"]:
            snippets = [
                f"[{i+1}] {res.get('title', '')}\n{res.get('snippet', '')}"
                for i, res in enumerate(results["organic_results"][:3])
            ]
            return "\n\n".join(snippets)

        return f"对不起，没有找到关于 '{query}' 的信息。"


# 使用示例
if __name__ == '__main__':
    search_tool = SearchTool()
    result = search_tool.run("Python编程语言")
    print(result)
