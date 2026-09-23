"""TTL + LRU 缓存

为上下文构建提供带过期与容量控制的键值缓存，并记录命中统计。

典型用法：
    from hello_agents.context import TTLCache

    cache = TTLCache(max_size=128, ttl_seconds=600)
    cache.put("k", "v")
    cache.get("k")   # -> "v"
    cache.stats()    # -> {"hits": 1, "misses": 0, "size": 1, "evictions": 0}
"""

from collections import OrderedDict
from time import monotonic
from typing import Generic, TypeVar

__all__ = ["TTLCache"]

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """带 TTL 与 LRU 淘汰的缓存

    Attributes:
        max_size: 最大条目数
        ttl_seconds: 条目存活秒数
    """

    def __init__(self, max_size: int = 256, ttl_seconds: float = 3600.0) -> None:
        if max_size <= 0:
            raise ValueError("max_size 必须为正整数")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须为正数")
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _is_expired(self, created_at: float, now: float) -> bool:
        return now - created_at > self.ttl_seconds

    def _purge_expired(self, now: float) -> None:
        stale = [
            key
            for key, (created_at, _) in self._data.items()
            if self._is_expired(created_at, now)
        ]
        for key in stale:
            del self._data[key]

    def get(self, key: K) -> V | None:
        """读取缓存，未命中或已过期返回 None"""
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return None
        created_at, value = entry
        if self._is_expired(created_at, monotonic()):
            del self._data[key]
            self._misses += 1
            return None
        self._data.move_to_end(key)
        self._hits += 1
        return value

    def put(self, key: K, value: V) -> None:
        """写入缓存，超容量时先清过期项、再按 LRU 淘汰"""
        now = monotonic()
        if key in self._data:
            self._data.move_to_end(key)
            self._data[key] = (now, value)
            return
        self._purge_expired(now)
        while len(self._data) >= self.max_size:
            self._data.popitem(last=False)
            self._evictions += 1
        self._data[key] = (now, value)

    def clear(self) -> None:
        """清空所有条目与统计计数"""
        self._data.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> dict[str, int]:
        """返回 {"hits", "misses", "size", "evictions"}"""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._data),
            "evictions": self._evictions,
        }
