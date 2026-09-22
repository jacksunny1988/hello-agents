"""RAG 工具

让 Agent 具备基于文档的智能问答能力：ingest 导入文档/文本，
query 检索相关片段并（可选）生成答案。
"""

import json
from typing import Any, Self

from ...memory.rag.pipeline import RAGPipeline
from ..base import BaseTool, ToolParameter
from ..response import ToolResponse

_ACTIONS = {"ingest", "query"}


class RAGTool(BaseTool):
    """RAG 工具（智能问答能力）"""

    def __init__(self, pipeline: RAGPipeline | None = None, llm: Any | None = None):
        super().__init__(
            name="rag",
            description=(
                "RAG 文档问答工具："
                "action=ingest 导入本地文档或纯文本；"
                "action=query 基于已导入内容回答问题。"
            ),
        )
        self.pipeline = pipeline or RAGPipeline(llm=llm)
        self._owns_pipeline = pipeline is None

    def run(self, input_data: Any = None, **kwargs) -> ToolResponse:
        """执行 RAG 操作

        input_data 支持 dict、JSON 字符串或纯文本
        （纯文本默认按 action=query 视为问题）。
        """
        params = self._parse_input(input_data, kwargs)
        action = params.get("action", "query")

        if action not in _ACTIONS:
            return ToolResponse.error(
                code="INVALID_PARAM",
                message=f"未知 action: {action}，可选 {sorted(_ACTIONS)}",
            )

        try:
            if action == "ingest":
                return self._do_ingest(params)
            return self._do_query(params)
        except Exception as error:  # 解析/检索异常不应击穿 Agent 循环
            return ToolResponse.error(code="RAG_ERROR", message=str(error))

    def get_parameters(self) -> list[ToolParameter]:
        """获取工具参数定义"""
        return [
            ToolParameter(
                name="action",
                type="str",
                description="操作类型: ingest / query",
                required=True,
            ),
            ToolParameter(
                name="source",
                type="str",
                description="文档路径（action=ingest 导入本地文件时使用）",
                required=False,
            ),
            ToolParameter(
                name="content",
                type="str",
                description="纯文本内容（action=ingest 不指定 source 时使用）",
                required=False,
            ),
            ToolParameter(
                name="question",
                type="str",
                description="要提问的问题（action=query 时使用）",
                required=False,
            ),
            ToolParameter(
                name="top_k",
                type="int",
                description="检索片段条数",
                required=False,
                default=5,
            ),
        ]

    def _do_ingest(self, params: dict[str, Any]) -> ToolResponse:
        source = params.get("source")
        content = params.get("content")
        if source:
            chunk_count = self.pipeline.ingest_file(source)
            detail = f"文档 {source}"
        elif content:
            chunk_count = self.pipeline.ingest_text(content)
            detail = "纯文本"
        else:
            return ToolResponse.error(
                code="INVALID_PARAM",
                message="action=ingest 需要提供 source（文件路径）或 content（文本）",
            )
        return ToolResponse.success(
            text=f"已导入 {detail}，共 {chunk_count} 个分块。",
            data={"chunk_count": chunk_count, "source": source},
        )

    def _do_query(self, params: dict[str, Any]) -> ToolResponse:
        question = params.get("question") or params.get("content")
        if not question:
            return ToolResponse.error(
                code="INVALID_PARAM", message="action=query 需要提供 question"
            )
        top_k = params.get("top_k")
        result = self.pipeline.query(
            question,
            top_k=int(top_k) if top_k is not None else None,
        )
        return ToolResponse.success(
            text=result.answer,
            data={
                "question": result.question,
                "generated": result.generated,
                "chunks": [chunk.to_dict() for chunk in result.chunks],
            },
        )

    @staticmethod
    def _parse_input(input_data: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """把 input_data 规整为参数字典（dict / JSON / 纯文本）"""
        params: dict[str, Any] = dict(kwargs)
        if input_data is None:
            return params
        if isinstance(input_data, dict):
            params.update(input_data)
            return params
        text = str(input_data).strip()
        if text.startswith("{"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                params.setdefault("question", text)
            else:
                if not isinstance(loaded, dict):
                    params.setdefault("question", text)
                else:
                    params.update(loaded)
            return params
        params.setdefault("action", "query")
        params.setdefault("question", text)
        return params

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        if self._owns_pipeline:
            self.pipeline.close()
