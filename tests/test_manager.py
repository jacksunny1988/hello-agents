"""MemoryManager 测试：写入分流、跨类型检索、记忆固化"""

import pytest

from hello_agents.memory import MemoryType


@pytest.mark.asyncio
async def test_remember_routes_by_type(manager):
    for memory_type in MemoryType:
        item_id = await manager.remember(
            f"内容-{memory_type.value}", memory_type=memory_type
        )
        assert item_id

    counts = await manager.count()
    assert counts == {
        "working": 1,
        "episodic": 1,
        "semantic": 1,
        "perceptual": 1,
    }


@pytest.mark.asyncio
async def test_recall_merges_across_types(manager):
    await manager.remember("用户喜欢爬山", memory_type=MemoryType.WORKING)
    await manager.remember("用户上周去了黄山", memory_type=MemoryType.EPISODIC)
    await manager.remember("飞机是交通工具", memory_type=MemoryType.SEMANTIC)

    hits = await manager.recall("用户", limit=5)

    assert hits
    assert all(hit.score is not None for hit in hits)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_recall_filters_by_types(manager):
    await manager.remember("工作记忆内容", memory_type=MemoryType.WORKING)
    await manager.remember("情景记忆内容", memory_type=MemoryType.EPISODIC)

    hits = await manager.recall("记忆内容", memory_types=[MemoryType.EPISODIC], limit=5)
    assert {hit.memory_type for hit in hits} == {MemoryType.EPISODIC}


@pytest.mark.asyncio
async def test_consolidate_high_importance_working_memory(manager):
    item_id = await manager.remember(
        "关键约束：API 限流 100 QPS",
        memory_type=MemoryType.WORKING,
        metadata={"importance": 0.9},
    )
    assert item_id

    counts = await manager.count()
    assert counts["episodic"] == 1  # 高重要度已固化

    hits = await manager.recall("API 限流", memory_types=[MemoryType.EPISODIC], limit=5)
    assert any("100 QPS" in hit.content for hit in hits)
    assert hits[0].metadata.get("source") == "consolidation"


@pytest.mark.asyncio
async def test_low_importance_not_consolidated(manager):
    await manager.remember(
        "临时闲聊", memory_type=MemoryType.WORKING, metadata={"importance": 0.1}
    )
    counts = await manager.count()
    assert counts["episodic"] == 0


@pytest.mark.asyncio
async def test_consolidate_flag_overrides_metadata(manager):
    await manager.remember("强制固化", memory_type=MemoryType.WORKING, consolidate=True)
    counts = await manager.count()
    assert counts["episodic"] == 1


@pytest.mark.asyncio
async def test_semantic_dedup(manager):
    first = await manager.remember("水在常温下是液体", memory_type="semantic")
    second = await manager.remember("水在常温下是液体", memory_type="semantic")

    assert first == second
    counts = await manager.count()
    assert counts["semantic"] == 1


@pytest.mark.asyncio
async def test_forget_spans_types(manager):
    working_id = await manager.remember("待删-工作", memory_type="working")
    episodic_id = await manager.remember("待删-情景", memory_type="episodic")

    assert await manager.forget(working_id) is True
    assert await manager.forget(episodic_id) is True
    assert await manager.forget("nope") is False

    counts = await manager.count()
    assert counts["working"] == 0
    assert counts["episodic"] == 0


@pytest.mark.asyncio
async def test_clear_all_or_by_type(manager):
    await manager.remember("a", memory_type="working")
    await manager.remember("b", memory_type="episodic")

    assert await manager.clear("working") == 1
    assert (await manager.count())["episodic"] == 1

    assert await manager.clear() >= 1
    assert sum((await manager.count()).values()) == 0
