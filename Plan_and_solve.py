import sys
import os
import re
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
from typing import List, Dict, Any
import ast
from llm_client import HelloAgentsLLM

load_dotenv()

PLANNER_PROMPT_TEMPLATE = """
你是一个顶级的AI规划专家。你的任务是将用户提出的复杂问题分解成一个由多个简单步骤组成的行动计划。
请确保计划中的每个步骤都是一个独立的、可执行的子任务，并且严格按照逻辑顺序排列。
你的输出必须是一个Python列表，其中每个元素都是一个描述子任务的字符串。

问题: {question}

请严格按照以下格式输出你的计划,```python与```作为前后缀是必要的:
```python
["步骤1", "步骤2", "步骤3", ...]
```
"""
class Planner:
    """
    """
    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client

    def plan(self, question: str) -> List[str]:
        """
        使用大语言模型将复杂问题分解为可执行的行动计划。

        :param question: 用户提出的复杂问题。
        :return: 一个字符串列表，每个元素都是一个独立的子任务。
        """
        prompt = PLANNER_PROMPT_TEMPLATE.format(question=question)
        messages = [{"role": "user", "content": prompt}]
        print("--- 正在生成计划 ---")
        response_text = self.llm_client.think(messages=messages)
        print(f"✅ 计划已生成:\n{response_text}")

        # 提取代码块中的内容
        code_block_match = re.search(r"```python(.*?)```", response_text, re.DOTALL)
        if not code_block_match:
            raise ValueError("未能从LLM响应中提取到有效的Python代码块。")

        code_block_content = code_block_match.group(1).strip()

        try:
            # 将字符串解析为Python列表
            plan_list = ast.literal_eval(code_block_content)
            if not isinstance(plan_list, list):
                raise ValueError("解析后的内容不是一个列表。")
            return plan_list
        except Exception as e:
            print(f"❌ 解析计划时出错: {e}")
            print(f"原始响应: {response_text}")
            raise ValueError(f"解析LLM响应时发生错误: {e}")

"""
在规划器 (Planner) 生成了清晰的行动蓝图后，我们就需要一个执行器 (Executor) 
来逐一完成计划中的任务。执行器不仅负责调用大语言模型来解决每个子问题，
还承担着一个至关重要的角色：状态管理。它必须记录每一步的执行结果，
并将其作为上下文提供给后续步骤，确保信息在整个任务链条中顺畅流动

执行器的提示词与规划器不同。它的目标不是分解问题，而是在已有上下文的基础上，
专注解决当前这一个步骤。因此，提示词需要包含以下关键信息：
- 原始问题： 确保模型始终了解最终目标。
- 完整计划： 让模型了解当前步骤在整个任务中的位置。
- 历史步骤与结果： 提供至今为止已经完成的工作，作为当前步骤的直接输入。
- 当前步骤： 明确指示模型现在需要解决哪一个具体任务。
"""

EXECUTOR_PROMPT_TEMPLATE = """
你是一位顶级的AI执行专家。你的任务是严格按照给定的计划，一步步地解决问题。
你将收到原始问题、完整的计划、以及到目前为止已经完成的步骤和结果。
请你专注于解决“当前步骤”，并仅输出该步骤的最终答案，不要输出任何额外的解释或对话。

# 原始问题:
{question}

# 完整计划:
{plan}

# 历史步骤与结果:
{history}

# 当前步骤:
{current_step}

请仅输出针对“当前步骤”的回答:
"""

class Executor:
    """
    执行器 (Executor) 负责逐步执行由规划器 (Planner)生成的行动计划。
    它调用大语言模型来解决每个子任务，并管理执行状态。
    """

    def __init__(self, llm_client: HelloAgentsLLM):
        self.llm_client = llm_client

    def execute(self, question: str, plan: List[str]) -> List[str]:
        """
        执行给定的计划，逐步解决问题。

        :param question: 用户提出的复杂问题。
        :param plan: 由Planner生成的行动计划列表。
        :return: 每个步骤的执行结果列表。
        """
        history = []
        results = []

        for step in plan:
            print(f"\n--- 正在执行步骤: {step} ---")
            history_str = "\n".join([f"步骤: {s}, 结果: {r}" for s, r in zip(plan[:len(results)], results)])
            prompt = EXECUTOR_PROMPT_TEMPLATE.format(
                question=question,
                plan=plan,
                history=history_str,
                current_step=step
            )
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.think(messages=messages)
            print(f"✅ 步骤: {step}, 结果:{response_text.strip()}")
            results.append(response_text.strip())
            history.append(f"步骤: {step}, 结果: {response_text.strip()}")

        return results

class PlanAndSolveAgent:
    """
    计划与执行代理 (PlanAndSolveAgent) 结合了规划器 (Planner) 和执行器 (Executor) 的功能，
    能够从复杂问题出发，生成行动计划并逐步执行，最终提供完整的解决方案。
    """

    def __init__(self, llm_client: HelloAgentsLLM):
        self.planner = Planner(llm_client)
        self.executor = Executor(llm_client)

    def plan_and_solve(self, question: str) -> List[str]:
        """
        从复杂问题出发，生成行动计划并逐步执行。

        :param question: 用户提出的复杂问题。
        :return: 每个步骤的执行结果列表。
        """
        print(f"📝 正在为问题生成计划: {question}")
        plan = self.planner.plan(question)
        if not plan:
            print("❌ 未能生成有效的行动计划。")
            raise ValueError("未能生成有效的行动计划。")
        
        print(f"✅ 生成的计划: {plan}")

        print(f"🚀 正在执行计划...")
        results = self.executor.execute(question, plan)
        print(f"🎯 执行完成，结果: {results}")

        return results


# --- 5. 主函数入口 ---
if __name__ == '__main__':
    try:
        llm_client = HelloAgentsLLM()
        agent = PlanAndSolveAgent(llm_client)
        question = "一个水果店周一卖出了15个苹果。周二卖出的苹果数量是周一的两倍。周三卖出的数量比周二少了5个。请问这三天总共卖出了多少个苹果？"
        agent.plan_and_solve(question)
    except ValueError as e:
        print(e)