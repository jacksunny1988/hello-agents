"""
搜索工具 - 提供网页搜索功能
"""

import os

from ..base import BaseTool, ToolParameter


class SearchTool(BaseTool):
    """
    搜索工具，基于SerpApi提供网页搜索功能。

    需要在环境变量中设置 SERPAPI_API_KEY。
    """

    def __init__(
        self,
        backend: str = "hybrid",
        tavily_key: str | None = None,
        serpapi_key: str | None = None,
    ):
        """
        初始化搜索工具。

        Args:
            backend: 搜索后端，可选值为 "hybrid"、"tavily" 或 "serpapi"
        """
        super().__init__(
            name="search",
            description="一个智能网页搜索引擎。支持混合搜索模式，自动选择最佳搜索源。",
        )
        self.backend = backend
        self.tavily_key = tavily_key or os.getenv("TAVILY_API_KEY")
        self.serpapi_key = serpapi_key or os.getenv("SERPAPI_API_KEY")
        self.search_sources = []
        self.tavily_client = None
        self._setup_search_sources()

    def _setup_search_sources(self):
        """设置可用的搜索源"""
        # 检查Tavily可用性
        if os.getenv("TAVILY_API_KEY"):
            try:
                from tavily import TavilyClient

                self.tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
                self.search_sources.append("tavily")
                print("✅ Tavily搜索源已启用")
            except ImportError:
                print("⚠️ Tavily库未安装")

        # 检查SerpApi可用性
        if os.getenv("SERPAPI_API_KEY"):
            try:
                from serpapi import Client

                self.search_sources.append("serpapi")
                print("✅ SerpApi搜索源已启用")
            except ImportError:
                print("⚠️ SerpApi库未安装")

        if self.search_sources:
            print(f"🔧 可用搜索源: {', '.join(self.search_sources)}")
        else:
            print("⚠️ 没有可用的搜索源，请配置API密钥")

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

        client = Client(api_key=self.serpapi_key)
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
        if results.get("organic_results"):
            snippets = [
                f"[{i + 1}] {res.get('title', '')}\n{res.get('snippet', '')}"
                for i, res in enumerate(results["organic_results"][:3])
            ]
            return "\n\n".join(snippets)

        return f"对不起，没有找到关于 '{query}' 的信息。"

    def _search_tavily(self, query: str) -> str:
        """使用Tavily搜索"""
        response = self.tavily_client.search(
            query=query, search_depth="basic", include_answer=True, max_results=3
        )

        result = f"🎯 Tavily AI搜索结果:{response.get('answer', '未找到直接答案')}\n\n"

        for i, item in enumerate(response.get("results", [])[:3], 1):
            result += f"[{i}] {item.get('title', '')}\n"
            result += f"    {item.get('content', '')[:200]}...\n"
            result += f"    来源: {item.get('url', '')}\n\n"

        return result

    def _search_hybrid(self, query: str) -> str:
        """混合搜索 - 智能选择最佳搜索源"""
        # 优先使用Tavily（AI优化的搜索）
        if "tavily" in self.search_sources:
            try:
                return self._search_tavily(query)
            except Exception as e:
                print(f"⚠️ Tavily搜索失败: {e}")
                # 如果Tavily失败，尝试SerpApi
                if "serpapi" in self.search_sources:
                    print("🔄 切换到SerpApi搜索")
                    return self._search_with_serpapi(query)

        # 如果Tavily不可用，使用SerpApi
        elif "serpapi" in self.search_sources:
            try:
                return self._search_with_serpapi(query)
            except Exception as e:
                print(f"⚠️ SerpApi搜索失败: {e}")

        # 如果都不可用，提示用户配置API
        return "❌ 没有可用的搜索源，请配置TAVILY_API_KEY或SERPAPI_API_KEY环境变量"

    def get_parameters(self):
        """获取工具参数定义"""
        return [
            ToolParameter(
                name="query",
                type="str",
                description="搜索查询字符串",
                required=True,
            )
        ]

    def run(self, query: str, **kwargs) -> str:
        """
        执行网页搜索。

        Args:
            query: 搜索查询字符串
            **kwargs: 额外参数

        Returns:
            搜索结果字符串
        """
        return self._search_hybrid(query)


# 使用示例
if __name__ == "__main__":
    search_tool = SearchTool()
    result = search_tool.run("Python编程语言")
    print(result)
