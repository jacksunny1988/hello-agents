"""TTLCache 测试：命中统计 / TTL 过期 / LRU 淘汰"""

import time

import pytest

from hello_agents.context.cache import TTLCache


def test_get_put_roundtrip_and_stats():
    cache = TTLCache(max_size=4, ttl_seconds=60)
    assert cache.get("missing") is None
    cache.put("k", "v")
    assert cache.get("k") == "v"
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["size"] == 1


def test_entry_expires_after_ttl():
    cache = TTLCache(max_size=4, ttl_seconds=0.05)
    cache.put("k", "v")
    time.sleep(0.08)
    assert cache.get("k") is None
    assert cache.stats()["size"] == 0


def test_lru_eviction_order():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # a 成为最近使用项
    cache.put("c", 3)  # 淘汰 b
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.stats()["evictions"] == 1


def test_put_purges_expired_before_evicting():
    cache = TTLCache(max_size=2, ttl_seconds=0.05)
    cache.put("a", 1)
    time.sleep(0.08)
    cache.put("b", 2)  # "a" 已过期，应被清理而非计入淘汰
    assert cache.stats()["size"] == 1
    assert cache.stats()["evictions"] == 0


def test_put_overwrites_without_growing():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1)
    cache.put("a", 2)
    assert cache.stats()["size"] == 1
    assert cache.get("a") == 2


def test_clear_resets_entries_and_counters():
    cache = TTLCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1)
    cache.get("a")
    cache.clear()
    assert cache.stats() == {"hits": 0, "misses": 0, "size": 0, "evictions": 0}


def test_invalid_arguments_rejected():
    with pytest.raises(ValueError):
        TTLCache(max_size=0)
    with pytest.raises(ValueError):
        TTLCache(ttl_seconds=0)
