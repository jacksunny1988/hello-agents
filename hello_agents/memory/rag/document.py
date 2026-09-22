"""文档处理器

多格式文档解析与分块：
- txt / md：标准库直接读取（零依赖）
- pdf / docx：可选依赖 pypdf / python-docx，缺失时抛出带安装提示的错误
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


@dataclass
class DocumentChunk:
    """文档分块结果"""

    content: str
    source: str
    index: int
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentProcessor:
    """文档解析与分块"""

    SUPPORTED_SUFFIXES: ClassVar[set[str]] = {
        ".txt",
        ".md",
        ".markdown",
        ".pdf",
        ".docx",
    }

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 64):
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def load(self, path: str | Path) -> str:
        """读取文档为纯文本，按扩展名分派解析器"""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"文档不存在: {file_path}")
        suffix = file_path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise ValueError(
                f"不支持的文档格式: {suffix}，可选 {sorted(self.SUPPORTED_SUFFIXES)}"
            )
        if suffix in {".txt", ".md", ".markdown"}:
            return self._load_text(file_path)
        if suffix == ".pdf":
            return self._load_pdf(file_path)
        return self._load_docx(file_path)

    def split(self, text: str, source: str = "") -> list[DocumentChunk]:
        """滑动窗口分块，保留块序号与来源"""
        chunks: list[DocumentChunk] = []
        step = self.chunk_size - self.chunk_overlap
        index = 0
        for start in range(0, max(len(text), 1), step):
            piece = text[start : start + self.chunk_size]
            if piece.strip():
                chunks.append(DocumentChunk(content=piece, source=source, index=index))
                index += 1
            if start + self.chunk_size >= len(text):
                break
        return chunks

    def process(self, path: str | Path) -> list[DocumentChunk]:
        """读取文档并分块，metadata 中记录来源路径"""
        file_path = Path(path)
        text = self.load(file_path)
        chunks = self.split(text, source=str(file_path))
        for chunk in chunks:
            chunk.metadata.setdefault("suffix", file_path.suffix.lower())
        return chunks

    @staticmethod
    def _load_text(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _load_pdf(path: Path) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ImportError(
                "解析 PDF 需要安装 pypdf: pip install 'hello-agents[rag]'"
            ) from error
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    @staticmethod
    def _load_docx(path: Path) -> str:
        try:
            import docx
        except ImportError as error:
            raise ImportError(
                "解析 DOCX 需要安装 python-docx: pip install 'hello-agents[rag]'"
            ) from error
        document = docx.Document(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
