"""工作记忆测试：TTL 过期、LRU 淘汰、子串检索"""

import pytest

from hello_agents.memory import MemoryItem, MemoryType, WorkingMemory


@pytest.mark.asyncio
async def test_add_and_get():
    memory = WorkingMemory(ttl_seconds=60, max_items=5)
    item_id = await memory.add(MemoryItem(content="记住用户的时区是 UTC+8"))

    item = await memory.get(item_id)
    assert item is not None
    assert item.content == "记住用户的时区是 UTC+8"
    assert item.memory_type == MemoryType.WORKING


@pytest.mark.asyncio
async def test_ttl_expiry():
    memory = WorkingMemory(ttl_seconds=0.01, max_items=5)
    item_id = await memory.add(MemoryItem(content="很快就会过期"))

    import asyncio

    await asyncio.sleep(0.02)

    assert await memory.get(item_id) is None
    assert await memory.count() == 0


@pytest.mark.asyncio
async def test_lru_eviction():
    memory = WorkingMemory(ttl_seconds=600, max_items=3)
    ids = [await memory.add(MemoryItem(content=f"item-{i}")) for i in range(5)]

    assert await memory.count() == 3
    assert await memory.get(ids[0]) is None  # 最旧两条被淘汰
    assert await memory.get(ids[1]) is None
    assert await memory.get(ids[4]) is not None


@pytest.mark.asyncio
async def test_search_by_substring_and_filters():
    memory = WorkingMemory(ttl_seconds=600, max_items=10)
    await memory.add(MemoryItem(content="用户喜欢蓝色", metadata={"topic": "color"}))
    await memory.add(MemoryItem(content="用户住在巴黎", metadata={"topic": "city"}))

    hits = await memory.search("蓝色")
    assert [item.content for item in hits] == ["用户喜欢蓝色"]

    hits = await memory.search("", filters={"topic": "city"})
    assert [item.content for item in hits] == ["用户住在巴黎"]

    # 空查询返回最近写入在前
    hits = await memory.search("")
    assert len(hits) == 2
    assert hits[0].content == "用户住在巴黎"


@pytest.mark.asyncio
async def test_delete_and_clear():
    memory = WorkingMemory(ttl_seconds=600, max_items=10)
    item_id = await memory.add(MemoryItem(content="待删除"))

    assert await memory.delete(item_id) is True
    assert await memory.delete("missing") is False

    await memory.add(MemoryItem(content="a"))
    await memory.add(MemoryItem(content="b"))
    assert await memory.clear() == 2
    assert await memory.count() == 0
