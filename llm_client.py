import sys
import os
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Dict, Any

sys.stdout.reconfigure(encoding='utf-8')

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
            raise ValueError("模型ID、API密钥、服务地址和超时时间必须被提供或在.env文件中定义。")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)

    def think(self,messages: List[Dict[str, Any]], temperature: float = 0.7) -> str:
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
                stream=True  # 设置为 False 以获取完整响应，而不是流式响应 
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

# --- 客户端使用示例 ---
if __name__ == '__main__':
    try:
        llmClient = HelloAgentsLLM()
        
        exampleMessages = [
            {"role": "system", "content": "You are a helpful assistant that writes Python code."},
            {"role": "user", "content": "写一个快速排序算法"}
        ]
        
        print("--- 调用LLM ---")
        responseText = llmClient.think(exampleMessages)
        if responseText:
            print("\n\n--- 完整模型响应 ---")
            print(responseText)

    except ValueError as e:
        print(e)