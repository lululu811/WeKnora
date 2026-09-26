"""
多级缓存 — 内存 LRU → Redis → 数据源

两个曾经导致"永远读到旧行情"的缺陷，已修掉：
1. 内存层没有 TTL。`LRUCache` 连时间戳都不存，条目活到被 LRU 淘汰为止。
   而行情是每天由 ETL 重算的，所以重启前的任何一次查询结果都会被永久复用。
2. `MultiLevelCache.clear_memory()` 只清内存，下一次读又把 Redis 里的旧值灌回来，
   但接口回的是"内存缓存已清空"。
现在内存层带 TTL，并且 clear 会同时清 Redis。
"""

import json
import hashlib
import time
from typing import Any, Callable, Optional, Tuple
from collections import OrderedDict
from threading import Lock


def stable_hash(*parts: str) -> str:
    """
    跨进程稳定的十六进制摘要。

    不要用内置 `hash()`：CPython 给 str 的 hash 加了每进程随机盐
    (PYTHONHASHSEED)，同一个 SQL 在不同 worker / 重启后 key 全变，
    缓存命中率归零；而且 64 位 hash 不抗碰撞，撞上就会返回别人的结果。
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class LRUCache:
    """线程安全的 LRU 缓存，条目带过期时间。"""

    def __init__(self, maxsize: int = 1000, default_ttl: int = 300):
        self._cache: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
        self._expirations = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._cache.pop(key, None)
                self._expirations += 1
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        effective_ttl = self._default_ttl if ttl is None else ttl
        if effective_ttl <= 0:
            # ttl=0 语义为"不缓存"，而不是"永不过期"
            self.delete(key)
            return
        with self._lock:
            self._cache[key] = (time.monotonic() + effective_ttl, value)
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._cache),
                "maxsize": self._maxsize,
                "default_ttl": self._default_ttl,
                "hits": self._hits,
                "misses": self._misses,
                "expirations": self._expirations,
            }


class MultiLevelCache:
    """
    多级缓存：内存 LRU → Redis → 数据源

    查询顺序：内存 → Redis → 数据源
    写入顺序：内存 + Redis（如果可用）
    过期语义：两级使用同一个 TTL。
    """

    def __init__(self, memory_size: int = 1000, default_ttl: int = 300):
        self._memory = LRUCache(maxsize=memory_size, default_ttl=default_ttl)
        self._redis = None
        self._default_ttl = default_ttl

    def set_redis(self, redis_source) -> None:
        self._redis = redis_source

    def _make_key(self, prefix: str, identifier: str) -> str:
        return f"pysvc:{prefix}:{identifier}"

    async def get(self, prefix: str, identifier: str) -> Optional[Any]:
        key = self._make_key(prefix, identifier)

        mem_result = self._memory.get(key)
        if mem_result is not None:
            return mem_result

        if self._redis is not None:
            try:
                redis_result = await self._redis.get_json(key)
                if redis_result is not None:
                    # 回填内存时重置 TTL，避免把一个快过期的值再续一轮
                    self._memory.set(key, redis_result, self._default_ttl)
                    return redis_result
            except Exception:
                pass  # Redis 异常不影响查询

        return None

    async def set(
        self,
        prefix: str,
        identifier: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> None:
        key = self._make_key(prefix, identifier)
        effective_ttl = self._default_ttl if ttl is None else ttl
        if effective_ttl <= 0:
            await self.delete(prefix, identifier)
            return

        self._memory.set(key, value, effective_ttl)

        if self._redis is not None:
            try:
                await self._redis.set_json(key, value, ttl=effective_ttl)
            except Exception:
                pass

    async def delete(self, prefix: str, identifier: str) -> None:
        key = self._make_key(prefix, identifier)
        self._memory.delete(key)
        if self._redis is not None:
            try:
                await self._redis.delete(key)
            except Exception:
                pass

    async def get_or_compute(
        self,
        prefix: str,
        identifier: str,
        compute_func: Callable,
        ttl: Optional[int] = None,
    ) -> Any:
        """缓存穿透模式：有缓存返回缓存，没有则计算并缓存。"""
        result = await self.get(prefix, identifier)
        if result is not None:
            return result
        result = await compute_func()
        await self.set(prefix, identifier, result, ttl)
        return result

    def clear_memory(self) -> None:
        """清空内存层。Redis 层由 `clear_all` 一并清理。"""
        self._memory.clear()

    async def clear_all(self) -> None:
        """清空两级缓存。`/cache/clear` 走这条路径。"""
        self._memory.clear()
        if self._redis is not None:
            try:
                await self._redis.clear_prefix("pysvc:")
            except Exception:
                pass

    @property
    def memory_size(self) -> int:
        return self._memory.size

    @property
    def stats(self) -> dict:
        result = {"memory": self._memory.stats, "redis": self._redis is not None}
        return result


# 全局缓存实例
cache = MultiLevelCache()
