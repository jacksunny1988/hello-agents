"""文档处理测试：txt/md 解析、分块边界、错误提示"""

import pytest

from hello_agents.memory.rag import DocumentProcessor


def test_load_txt_and_md(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("纯文本文档内容", encoding="utf-8")
    md = tmp_path / "readme.md"
    md.write_text("# 标题\n\n文档正文", encoding="utf-8")

    processor = DocumentProcessor(chunk_size=512, chunk_overlap=64)
    assert processor.load(txt) == "纯文本文档内容"
    assert processor.load(md) == "# 标题\n\n文档正文"


def test_split_respects_overlap():
    processor = DocumentProcessor(chunk_size=10, chunk_overlap=2)
    text = "0123456789ABCDEFGHIJ"  # 20 字符，步长 8

    chunks = processor.split(text, source="demo")

    assert [chunk.content for chunk in chunks] == [
        "0123456789",  # [0:10]
        "89ABCDEFGH",  # [8:18]，与上一块重叠 "89"
        "GHIJ",  # [16:26]，与上一块重叠 "GH"
    ]
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert all(chunk.source == "demo" for chunk in chunks)


def test_split_skips_whitespace_only_chunks():
    processor = DocumentProcessor(chunk_size=5, chunk_overlap=0)
    chunks = processor.split("abcde     fghij", source="ws")
    # 中间 5 个空格的块被丢弃
    assert [chunk.content for chunk in chunks] == ["abcde", "fghij"]


def test_process_returns_source_metadata(tmp_path):
    doc = tmp_path / "report.txt"
    doc.write_text("结论部分", encoding="utf-8")

    chunks = DocumentProcessor(chunk_size=512, chunk_overlap=64).process(doc)

    assert len(chunks) == 1
    assert chunks[0].metadata["suffix"] == ".txt"
    assert chunks[0].source == str(doc)


def test_missing_file_raises(tmp_path):
    processor = DocumentProcessor()
    with pytest.raises(FileNotFoundError):
        processor.load(tmp_path / "ghost.txt")


def test_unsupported_suffix_raises(tmp_path):
    doc = tmp_path / "image.png"
    doc.write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="不支持的文档格式"):
        DocumentProcessor().load(doc)


def test_invalid_overlap_rejected():
    with pytest.raises(ValueError, match="chunk_overlap"):
        DocumentProcessor(chunk_size=10, chunk_overlap=10)


def test_pdf_without_dependency_gives_install_hint(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    try:
        import pypdf  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="hello-agents\\[rag\\]"):
            DocumentProcessor().load(pdf)
    else:
        pytest.skip("pypdf 已安装，跳过缺失依赖提示测试")
