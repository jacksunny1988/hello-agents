"""SQLite 文档存储测试：CRUD、过滤、计数"""

from datetime import timedelta

import pytest

from hello_agents.memory import MemoryItem, MemoryType
from hello_agents.memory.base import utcnow
from hello_agents.memory.storage import DocumentStore


@pytest.fixture
def store(tmp_path):
    doc_store = DocumentStore(sqlite_path=str(tmp_path / "docs.db"))
    yield doc_store
    doc_store.close()


def test_add_get_roundtrip(store):
    item = MemoryItem(content="第一条事件", metadata={"session_id": "s1"})
    store.add(item)

    loaded = store.get(item.id)
    assert loaded is not None
    assert loaded.content == "第一条事件"
    assert loaded.metadata == {"session_id": "s1"}
    assert loaded.memory_type == MemoryType.WORKING


def test_replace_on_duplicate_id(store):
    item = MemoryItem(content="原始内容")
    store.add(item)
    item.content = "更新内容"
    store.add(item)

    assert store.count() == 1
    assert store.get(item.id).content == "更新内容"


def test_search_by_query_and_filters(store):
    store.add(MemoryItem(content="部署到生产环境", metadata={"env": "prod"}))
    store.add(MemoryItem(content="部署到测试环境", metadata={"env": "staging"}))

    hits = store.search("生产")
    assert [item.content for item in hits] == ["部署到生产环境"]

    hits = store.search("", filters={"env": "staging"})
    assert [item.content for item in hits] == ["部署到测试环境"]


def test_search_by_time_range(store):
    old = MemoryItem(content="旧事件", created_at=utcnow() - timedelta(days=3))
    store.add(old)
    store.add(MemoryItem(content="新事件"))

    boundary = (utcnow() - timedelta(days=1)).isoformat()
    hits = store.search("", filters={"after": boundary})
    assert [item.content for item in hits] == ["新事件"]

    hits = store.search("", filters={"before": boundary})
    assert [item.content for item in hits] == ["旧事件"]


def test_count_and_clear_by_type(store):
    store.add(MemoryItem(content="w", memory_type=MemoryType.WORKING))
    store.add(MemoryItem(content="e", memory_type=MemoryType.EPISODIC))

    assert store.count() == 2
    assert store.count(MemoryType.WORKING) == 1

    assert store.clear(MemoryType.WORKING) == 1
    assert store.count() == 1
    assert store.clear() == 1


def test_delete(store):
    item = MemoryItem(content="将被删除")
    store.add(item)
    assert store.delete(item.id) is True
    assert store.delete("missing") is False
    assert store.get(item.id) is None
