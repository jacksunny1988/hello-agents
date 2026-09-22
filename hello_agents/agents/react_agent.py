import re
from typing import Optional, List, Tuple
from hello_agents.core import Agent, Config
from hello_agents.core.llm import HelloAgentsLLM

MY_REACT_PROMPT = """你是一个具备推理和行动能力的AI助手。你可以通过思考分析问题，然后调用合适的工具来获取信息，最终给出准确的答案。

## 可用工具
{tools}

## 工作流程
请严格按照以下格式进行回应，每次只能执行一个步骤:

Thought: 分析当前问题，思考需要什么信息或采取什么行动。
Action: 选择一个行动，格式必须是以下之一:
- `{{tool_name}}[{{tool_input}}]` - 调用指定工具
- `Finish[最终答案]` - 当你有足够信息给出最终答案时

## 重要提醒
1. 每次回应必须包含Thought和Action两部分
2. 工具调用的格式必须严格遵循:工具名[参数]
3. 只有当你确信有足够信息回答问题时，才使用Finish
4. 如果工具返回的信息不够，继续使用其他工具或相同工具的不同参数

## 当前任务
**Question:** {question}

## 执行历史
{history}

现在开始你的推理和行动:
"""


class ReActAgent(Agent):
    """ReAct Agent - 通过推理(Reasoning)和行动(Acting)交替执行来解决问题

    特性：
    - Thought-Action-Observation 循环
    - 支持多种工具调用
    - 自动解析工具调用和最终答案
    """

    def __init__(
        self,
        name: str,
        llm_client: HelloAgentsLLM,
        tool_registry,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5,
        custom_prompt: Optional[str] = None
    ):
        super().__init__(name, llm_client, config)
        self.system_prompt = system_prompt
        self.tool_registry = tool_registry
        self.max_steps = max_steps
        self.current_history: List[str] = []
        self.prompt_template = custom_prompt if custom_prompt else MY_REACT_PROMPT
        print(f"✅ {name} 初始化完成，最大步数: {max_steps}")

    def run(self, input_text: str, **kwargs) -> str:
        """运行ReAct Agent"""
        self.current_history = []
        current_step = 0

        print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

        while current_step < self.max_steps:
            current_step += 1
            print(f"\n--- 第 {current_step} 步 ---")

            # 1. 构建提示词
            tools_desc = self.tool_registry.get_tools_description()
            history_str = "\n".join(self.current_history)
            prompt = self.prompt_template.format(
                tools=tools_desc,
                question=input_text,
                history=history_str
            )

            # 2. 调用LLM
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.think(messages, **kwargs)
            if not response_text:
                print("❌ LLM未返回有效响应")
                break

            # 3. 解析输出
            thought, action = self._parse_output(response_text)
            if thought:
                print(f"🤔 思考: {thought}")
            if not action:
                print("⚠️ 未能解析出有效的Action")
                break

            # 4. 检查完成条件
            if action.startswith("Finish"):
                final_answer = self._parse_action_input(action)
                print(f"🎉 最终答案: {final_answer}")
                self.add_message("user", input_text)
                self.add_message("assistant", final_answer)
                return final_answer

            # 5. 执行工具调用
            tool_name, tool_input = self._parse_action(action)
            if not tool_name or not tool_input:
                self.current_history.append(f"Action: {action}")
                self.current_history.append("Observation: 无效的Action格式，请检查。")
                continue

            print(f"🎬 行动: {tool_name}[{tool_input}]")
            try:
                observation = self.tool_registry.execute_tool(tool_name, tool_input)
            except Exception as e:
                observation = f"工具执行出错: {e}"

            print(f"👀 观察: {observation}")
            self.current_history.append(f"Action: {action}")
            self.current_history.append(f"Observation: {observation}")

        # 达到最大步数
        print("⚠️ 已达到最大步数，流程终止。")
        final_answer = "抱歉，我无法在限定步数内完成这个任务。"
        self.add_message("user", input_text)
        self.add_message("assistant", final_answer)
        return final_answer

    def _parse_output(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """解析LLM输出，提取Thought和Action

        Args:
            text: LLM返回的文本

        Returns:
            (thought, action) 元组
        """
        # Thought: 匹配到 Action: 或文本末尾
        thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", text, re.DOTALL)
        # Action: 匹配到文本末尾
        action_match = re.search(r"Action:\s*(.*?)$", text, re.DOTALL)

        thought = thought_match.group(1).strip() if thought_match else None
        action = action_match.group(1).strip() if action_match else None
        return thought, action

    def _parse_action(self, action_text: str) -> Tuple[Optional[str], Optional[str]]:
        """解析Action，提取工具名和参数

        Args:
            action_text: Action文本，如 "Search[Python编程]"

        Returns:
            (tool_name, tool_input) 元组
        """
        match = re.match(r"(\w+)\[(.*)\]", action_text, re.DOTALL)
        return (match.group(1), match.group(2)) if match else (None, None)

    def _parse_action_input(self, action_text: str) -> str:
        """解析Action中的输入内容

        Args:
            action_text: Action文本，如 "Finish[最终答案]"

        Returns:
            方括号内的内容
        """
        match = re.match(r"\w+\[(.*)\]", action_text, re.DOTALL)
        return match.group(1) if match else ""


# --- 客户端使用示例 ---
if __name__ == '__main__':
    from hello_agents.tools.registry import ToolRegistry
    from hello_agents.tools.builtin.search import SearchTool
    from hello_agents.tools.builtin.calculator import CalculatorTool

    # 初始化工具注册表并注册工具
    tool_registry = ToolRegistry()
    tool_registry.register_tool('search', SearchTool())
    tool_registry.register_tool('calculator', CalculatorTool())

    # 初始化LLM客户端
    llm_client = HelloAgentsLLM()

    # 创建ReActAgent实例
    agent = ReActAgent(
        name="ReAct助手",
        llm_client=llm_client,
        tool_registry=tool_registry,
        max_steps=5
    )

    # 运行Agent
    question = "苹果公司最新的iPhone型号是什么？它的起售价是多少？"
    result = agent.run(question)
    print(f"\n最终结果: {result}")
