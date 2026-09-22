"""
计算工具 - 提供数学计算功能
"""

import ast
import operator
from typing import Any

from ..base import BaseTool, ToolParameter


class CalculatorTool(BaseTool):
    """
    计算器工具，支持基本的数学运算。

    支持的运算符: +, -, *, /, //, %, **
    """

    def __init__(self):
        """初始化计算器工具"""
        super().__init__(
            name="calculator",
            description="一个计算器工具，用于执行数学运算。支持加减乘除、取余、幂运算等。",
        )
        # 支持的运算符
        self.operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
        }

    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""
        return [
            ToolParameter(
                name="expression",
                type="str",
                description="数学表达式，如 '2 + 3 * 4'",
                required=True,
            )
        ]

    def run(self, expression: str, **kwargs) -> str:
        """
        执行数学计算。

        Args:
            expression: 数学表达式，如 "2 + 3 * 4"
            **kwargs: 额外参数

        Returns:
            计算结果字符串
        """
        try:
            # 安全地解析和计算表达式
            result = self._safe_eval(expression)
            return f"{expression} = {result}"
        except Exception as e:
            return f"计算错误: {e!s}"

    def _safe_eval(self, expression: str) -> Any:
        """
        安全地计算数学表达式。

        Args:
            expression: 数学表达式

        Returns:
            计算结果
        """
        # 移除空格
        expression = expression.strip()

        # 解析表达式为AST
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            raise ValueError(f"无效的表达式: {expression}")

        # 递归计算AST节点
        return self._eval_node(tree.body)

    def _eval_node(self, node: ast.AST) -> Any:
        """
        递归计算AST节点。

        Args:
            node: AST节点

        Returns:
            节点的计算结果
        """
        # 数字常量
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Num):  # Python 3.7兼容
            return node.n

        # 二元运算
        elif isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            op_type = type(node.op)

            if op_type in self.operators:
                return self.operators[op_type](left, right)
            else:
                raise ValueError(f"不支持的运算符: {op_type.__name__}")

        # 一元运算（负号）
        elif isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -self._eval_node(node.operand)
            elif isinstance(node.op, ast.UAdd):
                return +self._eval_node(node.operand)
            else:
                raise TypeError(f"不支持的一元运算符: {type(node.op).__name__}")

        else:
            raise TypeError(f"不支持的表达式类型: {type(node).__name__}")


# 使用示例
if __name__ == "__main__":
    calc = CalculatorTool()
    print(calc.run("2 + 3"))
    print(calc.run("10 * 5 - 3"))
    print(calc.run("2 ** 10"))
    print(calc.run("17 / 3"))
