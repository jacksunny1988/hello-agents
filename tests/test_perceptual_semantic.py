"""语义/感知记忆测试：三元组、模态过滤、非法输入"""

import pytest

from hello_agents.memory import MemoryItem, MemoryType
from hello_agents.memory.storage import DocumentStore, InMemoryVectorStore
from hello_agents.memory.types import PerceptualMemory, SemanticMemory


@pytest.fixture
def semantic(embedding, tmp_path):
    memory = SemanticMemory(
        embedding=embedding,
        document_store=DocumentStore(sqlite_path=str(tmp_path / "sem.db")),
        vector_store=InMemoryVectorStore(dim=embedding.dim),
    )
    yield memory
    memory.close()


@pytest.fixture
def perceptual(embedding, tmp_path):
    memory = PerceptualMemory(
        embedding=embedding,
        document_store=DocumentStore(sqlite_path=str(tmp_path / "per.db")),
        vector_store=InMemoryVectorStore(dim=embedding.dim),
    )
    yield memory
    memory.close()


@pytest.mark.asyncio
async def test_semantic_stores_triple_metadata(semantic):
    item_id = await semantic.add(
        MemoryItem(
            content="Python 是一种编程语言",
            metadata={
                "subject": "Python",
                "relation": "是一种",
                "target": "编程语言",
            },
        )
    )

    item = await semantic.get(item_id)
    assert item.metadata["subject"] == "Python"
    assert item.metadata["target"] == "编程语言"


@pytest.mark.asyncio
async def test_semantic_dedup_near_duplicates(semantic):
    first = await semantic.add(MemoryItem(content="地球绕着太阳转"))
    second = await semantic.add(MemoryItem(content="地球绕着太阳转"))

    assert first == second
    assert await semantic.count() == 1


@pytest.mark.asyncio
async def test_semantic_search_by_content(semantic):
    await semantic.add(MemoryItem(content="Redis 是内存数据库"))
    hits = await semantic.search("内存数据库", limit=3)
    assert hits and "Redis" in hits[0].content


@pytest.mark.asyncio
async def test_perceptual_accepts_modalities(perceptual):
    for modality in ("text", "image", "audio", "video", "file"):
        item_id = await perceptual.add(
            MemoryItem(
                content=f"{modality} 资源的描述文本",
                metadata={"modality": modality, "uri": f"assets/{modality}.bin"},
            )
        )
        assert item_id
    assert await perceptual.count() == 5


@pytest.mark.asyncio
async def test_perceptual_filter_by_modality(perceptual):
    await perceptual.add(
        MemoryItem(
            content="用户上传了白板照片",
            metadata={"modality": "image", "uri": "assets/board.png"},
        )
    )
    await perceptual.add(
        MemoryItem(
            content="用户上传了会议录音",
            metadata={"modality": "audio", "uri": "assets/meeting.wav"},
        )
    )

    hits = await perceptual.search("上传", filters={"modality": "image"})
    assert [hit.metadata["modality"] for hit in hits] == ["image"]


@pytest.mark.asyncio
async def test_perceptual_rejects_unknown_modality(perceptual):
    with pytest.raises(ValueError, match="不支持的模态"):
        await perceptual.add(MemoryItem(content="x", metadata={"modality": "hologram"}))


@pytest.mark.asyncio
async def test_types_tag_memory_type(semantic, perceptual):
    s_id = await semantic.add(MemoryItem(content="知识"))
    p_id = await perceptual.add(
        MemoryItem(content="感知", metadata={"modality": "text"})
    )
    assert (await semantic.get(s_id)).memory_type == MemoryType.SEMANTIC
    assert (await perceptual.get(p_id)).memory_type == MemoryType.PERCEPTUAL
