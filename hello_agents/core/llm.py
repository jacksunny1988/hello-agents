"""
LLM统一接口 - 提供与大语言模型交互的统一接口
"""

import sys
import os
from typing import List, Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()


class HelloAgentsLLM:
    """
    HelloAgents LLM客户端，提供统一的LLM调用接口。
    支持多种LLM提供商（OpenAI、DeepSeek、智谱等）。
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
        timeout: Optional[int] = None
    ):
        """
        初始化LLM客户端。

        Args:
            model: 模型名称，默认从环境变量读取
            api_key: API密钥，默认从环境变量读取
            base_url: API基础URL，默认从环境变量读取
            provider: LLM提供商，可选值: openai, deepseek, zhipu, kimi等
            timeout: 请求超时时间（秒）
        """
        self.provider = provider or self._auto_detect_provider(api_key, base_url)
        self.model = model or os.getenv("LLM_MODEL_ID", "gpt-3.5-turbo")

        # 解析凭证
        resolved_api_key, resolved_base_url = self._resolve_credentials(api_key, base_url)

        self.client = OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=timeout or int(os.getenv("LLM_TIMEOUT", "30"))
        )

    def think(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        调用LLM进行思考/生成。

        Args:
            messages: 消息列表，每个消息包含role和content
            **kwargs: 额外参数

        Returns:
            LLM生成的文本响应
        """
        print(f"🧠 正在调用 {self.model} 模型...")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )
            content = response.choices[0].message.content
            print("✅ 大语言模型响应成功")
            return content
        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            raise RuntimeError(f"调用LLM API时发生错误: {e}")

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """
        聊天接口，与think方法相同。

        Args:
            messages: 消息列表
            **kwargs: 额外参数

        Returns:
            LLM生成的文本响应
        """
        return self.think(messages, **kwargs)

    def stream_invoke(self, messages: List[Dict[str, str]], **kwargs):
        """
        流式调用LLM，逐步返回生成的文本片段。

        Args:
            messages: 消息列表，每个消息包含role和content
            **kwargs: 额外参数

        Yields:
            str: LLM生成的文本片段（逐块返回）
        """
        print(f"🧠 正在调用 {self.model} 模型（流式）...")
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            print("✅ 大语言模型流式响应完成")
        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            raise RuntimeError(f"调用LLM API时发生错误: {e}")

    def _auto_detect_provider(self, api_key: Optional[str], base_url: Optional[str]) -> str:
        """
        自动检测LLM提供商
        """
        # 检查特定提供商的环境变量
        if os.getenv("MODELSCOPE_API_KEY"): return "modelscope"
        if os.getenv("OPENAI_API_KEY"): return "openai"
        if os.getenv("ZHIPU_API_KEY"): return "zhipu"
        if os.getenv("DEEPSEEK_API_KEY"): return "deepseek"
        if os.getenv("KIMI_API_KEY"): return "kimi"

        # 获取通用的环境变量
        actual_api_key = api_key or os.getenv("LLM_API_KEY")
        actual_base_url = base_url or os.getenv("LLM_BASE_URL")

        # 根据 base_url 判断
        if actual_base_url:
            base_url_lower = actual_base_url.lower()
            if "api-inference.modelscope.cn" in base_url_lower: return "modelscope"
            if "open.bigmodel.cn" in base_url_lower: return "zhipu"
            if "api.deepseek.com" in base_url_lower: return "deepseek"
            if "api.kimi.cn" in base_url_lower: return "kimi"
            if "localhost" in base_url_lower or "127.0.0.1" in base_url_lower:
                if ":11434" in base_url_lower: return "ollama"
                if ":8000" in base_url_lower: return "vllm"
                return "local"

        # 根据 API 密钥格式辅助判断
        if actual_api_key:
            if actual_api_key.startswith("ms-"): return "modelscope"

        return "auto"

    def _resolve_credentials(self, api_key: Optional[str], base_url: Optional[str]) -> tuple:
        """根据provider解析API密钥和base_url"""
        if self.provider == "openai":
            resolved_api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
            return resolved_api_key, resolved_base_url

        elif self.provider == "modelscope":
            resolved_api_key = api_key or os.getenv("MODELSCOPE_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "https://api-inference.modelscope.cn/v1/"
            return resolved_api_key, resolved_base_url
        elif self.provider == "zhipu":
            resolved_api_key = api_key or os.getenv("ZHIPU_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "https://open.bigmodel.cn/api/"
            return resolved_api_key, resolved_base_url
        elif self.provider == "deepseek":
            resolved_api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com/v1/"
            return resolved_api_key, resolved_base_url
        elif self.provider == "kimi":
            resolved_api_key = api_key or os.getenv("KIMI_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "https://api.kimi.cn/v1/"
            return resolved_api_key, resolved_base_url
        elif self.provider == "ollama":
            resolved_api_key = api_key or os.getenv("OLLAMA_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "http://localhost:11434/v1/"
            return resolved_api_key, resolved_base_url
        elif self.provider == "vllm":
            resolved_api_key = api_key or os.getenv("VLLM_API_KEY") or os.getenv("LLM_API_KEY")
            resolved_base_url = base_url or os.getenv("LLM_BASE_URL") or "http://localhost:8000/v1/"
            return resolved_api_key, resolved_base_url

        # 默认使用通用配置
        resolved_api_key = api_key or os.getenv("LLM_API_KEY")
        resolved_base_url = base_url or os.getenv("LLM_BASE_URL")
        return resolved_api_key, resolved_base_url


# --- 客户端使用示例 ---
if __name__ == '__main__':
    try:
        llmClient = HelloAgentsLLM()

        exampleMessages = [
            {"role": "system", "content": "You are a helpful assistant that writes Python code."},
            {"role": "user", "content": "写一个快速排序算法"}
        ]

        # 同步调用
        response = llmClient.think(exampleMessages)
        print(response)

        # 流式调用
        print("\n--- 流式响应示例 ---")
        for chunk in llmClient.stream_invoke(exampleMessages):
            print(chunk, end="", flush=True)
        print()
    except Exception as e:
        print(f"错误: {e}")
