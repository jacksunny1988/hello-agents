"""降级路径测试：缺 qdrant-client 时自动降级且功能不减"""

import pytest

from hello_agents.memory import MemoryConfig, MemoryItem, MemoryType
from hello_agents.memory.storage import (
    InMemoryVectorStore,
    QdrantVectorStore,
    create_vector_store,
)
from hello_agents.memory.storage.neo4j_store import create_neo4j_store


def test_create_vector_store_falls_back_without_qdrant(config):
    store = create_vector_store(config)

    try:
        import qdrant_client  # noqa: F401
    except ImportError:
        assert isinstance(store, InMemoryVectorStore)
    else:
        assert isinstance(store, QdrantVectorStore)


@pytest.mark.asyncio
async def test_in_memory_store_roundtrip():
    store = InMemoryVectorStore(dim=8)
    item = MemoryItem(
        content="测试",
        memory_type=MemoryType.EPISODIC,
        embedding=[1.0] + [0.0] * 7,
        metadata={"env": "prod"},
    )

    await store.upsert([item])
    hits = await store.search([1.0] + [0.0] * 7, limit=5)
    assert hits and hits[0][0] == item.id

    # metadata 过滤
    assert await store.search([1.0] + [0.0] * 7, filters={"env": "prod"})
    assert not await store.search([1.0] + [0.0] * 7, filters={"env": "dev"})


@pytest.mark.asyncio
async def test_in_memory_store_requires_embedding():
    store = InMemoryVectorStore(dim=4)
    with pytest.raises(ValueError, match="缺少 embedding"):
        await store.upsert([MemoryItem(content="无向量")])


@pytest.mark.asyncio
async def test_in_memory_store_delete_and_clear():
    store = InMemoryVectorStore(dim=4)
    items = [
        MemoryItem(content=f"i{n}", embedding=[float(n), 0.0, 0.0, 0.0]) for n in (1, 2)
    ]
    await store.upsert(items)

    assert await store.delete([items[0].id]) == 1
    assert await store.count() == 1
    assert await store.clear() == 1
    assert await store.count() == 0


def test_create_neo4j_store_returns_none_without_config():
    assert create_neo4j_store(MemoryConfig()) is None


def test_create_neo4j_store_returns_none_when_unreachable():
    config = MemoryConfig(
        neo4j_uri="bolt://127.0.0.1:7687", neo4j_password="wrong-password"
    )
    try:
        import neo4j  # noqa: F401
    except ImportError:
        pytest.skip("neo4j 未安装")
    assert create_neo4j_store(config) is None
