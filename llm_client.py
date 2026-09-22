import os
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

sys.stdout.reconfigure(encoding="utf-8")

# 加载 .env 文件中的环境变量
load_dotenv()


class HelloAgentsLLM:
    """
    A class to interact with the DeepSeek LLM API.
    Attributes:
        model (str): The model ID for the LLM.
        api_key (str): The API key for authentication.
        base_url (str): The base URL for the LLM API.
        timeout (int): The timeout for API requests in seconds.
    """

    def __init__(self):
        self.model = os.getenv("LLM_MODEL_ID")
        self.api_key = os.getenv("LLM_API_KEY")
        self.base_url = os.getenv("LLM_BASE_URL")
        self.timeout = int(os.getenv("LLM_TIMEOUT"))
        if not all([self.model, self.api_key, self.base_url, self.timeout]):
            raise ValueError(
                "模型ID、API密钥、服务地址和超时时间必须被提供或在.env文件中定义。"
            )
        self.client = OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )

    def think(self, messages: list[dict[str, Any]], temperature: float = 0.7) -> str:
        """
        Sends a request to the LLM API with the provided messages and returns the response.
        Args:
            messages (List[Dict[str, Any]]): A list of message dictionaries to send to the LLM.
            temperature (float): The sampling temperature for the response.
        Returns:
            str: The text response from the LLM.
        """
        print(f"🧠 正在调用 {self.model} 模型...")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                # max_tokens=max_tokens,
                stream=True,  # 设置为 False 以获取完整响应，而不是流式响应
            )
            # 处理流式响应
            print("✅ 大语言模型响应成功.")
            collect_content = []
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    collect_content.append(chunk.choices[0].delta.content)
            return "".join(collect_content)
        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            raise RuntimeError(f"调用LLM API时发生错误: {e}")

    def _auto_detect_provider(
        self, api_key: str | None, base_url: str | None
    ) -> str:
        """
        自动检测LLM提供商
        """
        # 1. 检查特定提供商的环境变量 (最高优先级)
        if os.getenv("MODELSCOPE_API_KEY"):
            return "modelscope"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("ZHIPU_API_KEY"):
            return "zhipu"
        if os.getenv("DEEPSEEK_API_KEY"):
            return "deepseek"
        if os.getenv("KIMI_API_KEY"):
            return "kimi"
        # ... 其他服务商的环境变量检查

        # 获取通用的环境变量
        actual_api_key = api_key or os.getenv("LLM_API_KEY")
        actual_base_url = base_url or os.getenv("LLM_BASE_URL")

        # 2. 根据 base_url 判断
        if actual_base_url:
            base_url_lower = actual_base_url.lower()
            if "api-inference.modelscope.cn" in base_url_lower:
                return "modelscope"
            if "open.bigmodel.cn" in base_url_lower:
                return "zhipu"
            if "api.deepseek.com" in base_url_lower:
                return "deepseek"
            if "api.kimi.cn" in base_url_lower:
                return "kimi"
            if "localhost" in base_url_lower or "127.0.0.1" in base_url_lower:
                if ":11434" in base_url_lower:
                    return "ollama"
                if ":8000" in base_url_lower:
                    return "vllm"
                return "local"  # 其他本地端口

        # 3. 根据 API 密钥格式辅助判断
        if actual_api_key and actual_api_key.startswith("ms-"):
            return "modelscope"
        # ... 其他密钥格式判断

        # 4. 默认返回 'auto'，使用通用配置
        return "auto"

    def _resolve_credentials(
        self, api_key: str | None, base_url: str | None
    ) -> tuple[str, str]:
        """根据provider解析API密钥和base_url"""
        if self.provider == "openai":
            resolved_api_key = (
                api_key or os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
            )
            return resolved_api_key, resolved_base_url

        elif self.provider == "modelscope":
            resolved_api_key = (
                api_key or os.getenv("MODELSCOPE_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url
                or os.getenv("LLM_BASE_URL")
                or "https://api-inference.modelscope.cn/v1/"
            )
            return resolved_api_key, resolved_base_url
        elif self.provider == "zhipu":
            resolved_api_key = (
                api_key or os.getenv("ZHIPU_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url or os.getenv("LLM_BASE_URL") or "https://open.bigmodel.cn/api/"
            )
            return resolved_api_key, resolved_base_url
        elif self.provider == "deepseek":
            resolved_api_key = (
                api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url or os.getenv("LLM_BASE_URL") or "https://api.deepseek.com/v1/"
            )
            return resolved_api_key, resolved_base_url
        elif self.provider == "kimi":
            resolved_api_key = (
                api_key or os.getenv("KIMI_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url or os.getenv("LLM_BASE_URL") or "https://api.kimi.cn/v1/"
            )
            return resolved_api_key, resolved_base_url
        elif self.provider == "ollama":
            resolved_api_key = (
                api_key or os.getenv("OLLAMA_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url or os.getenv("LLM_BASE_URL") or "http://localhost:11434/v1/"
            )
            return resolved_api_key, resolved_base_url
        elif self.provider == "vllm":
            resolved_api_key = (
                api_key or os.getenv("VLLM_API_KEY") or os.getenv("LLM_API_KEY")
            )
            resolved_base_url = (
                base_url or os.getenv("LLM_BASE_URL") or "http://localhost:8000/v1/"
            )
            return resolved_api_key, resolved_base_url

        # ... 其他服务商的逻辑


# --- 客户端使用示例 ---
if __name__ == "__main__":
    try:
        llmClient = HelloAgentsLLM()

        exampleMessages = [
            {
                "role": "system",
                "content": "You are a helpful assistant that writes Python code.",
            },
            {"role": "user", "content": "写一个快速排序算法"},
        ]

        print("--- 调用LLM ---")
        responseText = llmClient.think(exampleMessages)
        if responseText:
            print("\n\n--- 完整模型响应 ---")
            print(responseText)

    except ValueError as e:
        print(e)
