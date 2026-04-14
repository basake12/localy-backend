"""
app/core/cache.py

Redis caching layer.

Configuration is read from settings.REDIS_URL (parsed into host/port/password/db
via the convenience properties added to Settings in config.py).
"""
import json
import logging
from typing import Any, Callable, Optional
from functools import wraps

import redis

from app.config import settings

logger = logging.getLogger(__name__)


# ============================================
# REDIS CLIENT
# ============================================

class RedisCache:
    """Thread-safe Redis cache manager with graceful degradation."""

    def __init__(self) -> None:
        self.client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self) -> None:
        """Connect to Redis using settings parsed from REDIS_URL."""
        try:
            self.client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password,
                db=settings.redis_db,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
            self.client.ping()
            logger.info("Redis connection established.")
        except Exception as exc:
            logger.warning(f"Redis connection failed (cache disabled): {exc}")
            self.client = None

    # ------------------------------------------------------------------ #
    # Health                                                               #
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        if not self.client:
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Basic ops                                                            #
    # ------------------------------------------------------------------ #

    def get(self, key: str) -> Optional[Any]:
        if not self.is_available():
            return None
        try:
            value = self.client.get(key)
            if value is None:
                return None
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except Exception as exc:
            logger.error(f"Cache GET error for '{key}': {exc}")
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        if not self.is_available():
            return False
        try:
            serialised = (
                json.dumps(value)
                if isinstance(value, (dict, list))
                else str(value) if not isinstance(value, (str, int, float))
                else value
            )
            self.client.setex(key, ttl, serialised)
            return True
        except Exception as exc:
            logger.error(f"Cache SET error for '{key}': {exc}")
            return False

    def delete(self, key: str) -> bool:
        if not self.is_available():
            return False
        try:
            self.client.delete(key)
            return True
        except Exception as exc:
            logger.error(f"Cache DELETE error for '{key}': {exc}")
            return False

    def exists(self, key: str) -> bool:
        if not self.is_available():
            return False
        try:
            return bool(self.client.exists(key))
        except Exception:
            return False

    def increment(self, key: str, amount: int = 1) -> Optional[int]:
        if not self.is_available():
            return None
        try:
            return self.client.incr(key, amount)
        except Exception as exc:
            logger.error(f"Cache INCR error for '{key}': {exc}")
            return None

    def expire(self, key: str, ttl: int) -> bool:
        if not self.is_available():
            return False
        try:
            return bool(self.client.expire(key, ttl))
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Pattern ops                                                          #
    # ------------------------------------------------------------------ #

    def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching a glob pattern."""
        if not self.is_available():
            return 0
        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as exc:
            logger.error(f"Cache DELETE_PATTERN error for '{pattern}': {exc}")
            return 0

    def get_keys(self, pattern: str = "*") -> list:
        if not self.is_available():
            return []
        try:
            return self.client.keys(pattern)
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Hash ops                                                             #
    # ------------------------------------------------------------------ #

    def hset(self, name: str, key: str, value: Any) -> bool:
        if not self.is_available():
            return False
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            self.client.hset(name, key, value)
            return True
        except Exception as exc:
            logger.error(f"Cache HSET error for '{name}/{key}': {exc}")
            return False

    def hget(self, name: str, key: str) -> Optional[Any]:
        if not self.is_available():
            return None
        try:
            value = self.client.hget(name, key)
            if value is None:
                return None
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except Exception:
            return None

    def hgetall(self, name: str) -> dict:
        if not self.is_available():
            return {}
        try:
            return self.client.hgetall(name)
        except Exception:
            return {}

    # ------------------------------------------------------------------ #
    # List ops                                                             #
    # ------------------------------------------------------------------ #

    def lpush(self, key: str, *values: Any) -> bool:
        if not self.is_available():
            return False
        try:
            serialised = [
                json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for v in values
            ]
            self.client.lpush(key, *serialised)
            return True
        except Exception:
            return False

    def rpush(self, key: str, *values: Any) -> bool:
        if not self.is_available():
            return False
        try:
            serialised = [
                json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for v in values
            ]
            self.client.rpush(key, *serialised)
            return True
        except Exception:
            return False

    def lrange(self, key: str, start: int = 0, end: int = -1) -> list:
        if not self.is_available():
            return []
        try:
            values = self.client.lrange(key, start, end)
            return [
                json.loads(v) if v.startswith(("[", "{")) else v
                for v in values
            ]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Set ops                                                              #
    # ------------------------------------------------------------------ #

    def sadd(self, key: str, *members: Any) -> bool:
        if not self.is_available():
            return False
        try:
            self.client.sadd(key, *members)
            return True
        except Exception:
            return False

    def sismember(self, key: str, member: Any) -> bool:
        if not self.is_available():
            return False
        try:
            return bool(self.client.sismember(key, member))
        except Exception:
            return False

    def smembers(self, key: str) -> set:
        if not self.is_available():
            return set()
        try:
            return self.client.smembers(key)
        except Exception:
            return set()


# Singleton
cache = RedisCache()


# ============================================
# DECORATORS
# ============================================

def cached(ttl: int = 3600, key_prefix: str = ""):
    """
    Cache decorator — works with both sync and async functions.

    Usage:
        @cached(ttl=300, key_prefix="hotel")
        async def get_hotel(hotel_id: str): ...
    """
    def decorator(func: Callable):
        import asyncio

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            cache_key = _build_key(key_prefix, func.__name__, args, kwargs)
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            cache_key = _build_key(key_prefix, func.__name__, args, kwargs)
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return cached_val
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


def _build_key(prefix: str, name: str, args: tuple, kwargs: dict) -> str:
    parts = [prefix, name]
    parts.extend(str(a) for a in args)
    parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return ":".join(filter(None, parts))


def invalidate_cache(pattern: str):
    """
    Invalidate cache decorator — works with both sync and async functions.

    Usage:
        @invalidate_cache("hotel:*")
        async def update_hotel(...): ...
    """
    def decorator(func: Callable):
        import asyncio

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            cache.delete_pattern(pattern)
            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            cache.delete_pattern(pattern)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator