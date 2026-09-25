"""工作记忆

短时、高频的上下文缓存：TTL 自动过期 + LRU 容量淘汰，纯内存实现，
不依赖任何外部存储，进程退出即消失。
"""

from collections import OrderedDict
from datetime import timedelta
from typing import Any

from ..base import BaseMemory, MemoryItem, MemoryType, utcnow


class WorkingMemory(BaseMemory):
    """工作记忆（TTL + LRU，纯内存）"""

    memory_type = MemoryType.WORKING

    def __init__(self, ttl_seconds: float = 600.0, max_items: int = 50):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: OrderedDict[str, MemoryItem] = OrderedDict()

    async def add(self, item: MemoryItem) -> str:
        """写入记忆；未指定 expires_at 时按 TTL 推算，超容量时淘汰最旧条目"""
        self._evict_expired()
        item.memory_type = MemoryType.WORKING
        if item.expires_at is None and self.ttl_seconds > 0:
            item.expires_at = utcnow() + timedelta(seconds=self.ttl_seconds)

        self._items[item.id] = item
        self._items.move_to_end(item.id)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return item.id

    async def get(self, item_id: str) -> MemoryItem | None:
        """读取记忆；已过期则删除并返回 None"""
        self._evict_expired()
        item = self._items.get(item_id)
        if item is not None:
            self._items.move_to_end(item_id)
        return item

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """按子串匹配检索；查询为空时返回最近的记忆"""
        self._evict_expired()
        matched = []
        for item in reversed(self._items.values()):  # 新近优先
            if not self._match(item, query, filters):
                continue
            # 工作记忆无向量：命中子串记满分，仅按过滤/空查询列出记半分
            item.score = 1.0 if query and query.lower() in item.content.lower() else 0.5
            matched.append(item)
        return matched[: max(limit, 0)]

    async def delete(self, item_id: str) -> bool:
        """删除一条记忆"""
        return self._items.pop(item_id, None) is not None

    async def clear(self) -> int:
        """清空全部工作记忆"""
        size = len(self._items)
        self._items.clear()
        return size

    async def count(self) -> int:
        """返回当前条数（不含已过期）"""
        self._evict_expired()
        return len(self._items)

    def _evict_expired(self) -> None:
        """惰性清除已过期条目"""
        expired = [
            item_id for item_id, item in self._items.items() if item.is_expired()
        ]
        for item_id in expired:
            del self._items[item_id]

    @staticmethod
    def _match(item: MemoryItem, query: str, filters: dict[str, Any] | None) -> bool:
        if query and query.lower() not in item.content.lower():
            return False
        for key, value in (filters or {}).items():
            if key.startswith("_") or key in {"after", "before"}:
                continue
            if str(item.metadata.get(key)) != str(value):
                return False
        return True
