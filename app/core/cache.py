"""
Redis caching utilities.
"""
import json
import pickle
from typing import Any, Optional, Callable
from functools import wraps
import redis
from app.config import settings


# ============================================
# REDIS CLIENT
# ============================================

class RedisCache:
    """Redis cache manager."""

    def __init__(self):
        self.client: Optional[redis.Redis] = None
        self._connect()

    def _connect(self):
        """Connect to Redis."""
        try:
            self.client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                password=settings.REDIS_PASSWORD,
                db=settings.REDIS_DB,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # Test connection
            self.client.ping()
        except Exception as e:
            print(f"Redis connection failed: {e}")
            self.client = None

    def is_available(self) -> bool:
        """Check if Redis is available."""
        if not self.client:
            return False
        try:
            self.client.ping()
            return True
        except:
            return False

    # ==========================================
    # BASIC OPERATIONS
    # ==========================================

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache."""
        if not self.is_available():
            return None

        try:
            value = self.client.get(key)
            if value:
                # Try JSON first, fall back to string
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
            return None
        except Exception as e:
            print(f"Cache get error: {e}")
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set value in cache with TTL in seconds."""
        if not self.is_available():
            return False

        try:
            # Serialize complex objects to JSON
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            elif not isinstance(value, (str, int, float)):
                value = str(value)

            self.client.setex(key, ttl, value)
            return True
        except Exception as e:
            print(f"Cache set error: {e}")
            return False

    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if not self.is_available():
            return False

        try:
            self.client.delete(key)
            return True
        except Exception as e:
            print(f"Cache delete error: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self.is_available():
            return False

        try:
            return bool(self.client.exists(key))
        except:
            return False

    def increment(self, key: str, amount: int = 1) -> Optional[int]:
        """Increment counter."""
        if not self.is_available():
            return None

        try:
            return self.client.incr(key, amount)
        except:
            return None

    def expire(self, key: str, ttl: int) -> bool:
        """Set expiration on existing key."""
        if not self.is_available():
            return False

        try:
            return bool(self.client.expire(key, ttl))
        except:
            return False

    # ==========================================
    # PATTERN OPERATIONS
    # ==========================================

    def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern."""
        if not self.is_available():
            return 0

        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            print(f"Cache delete pattern error: {e}")
            return 0

    def get_keys(self, pattern: str = "*") -> list:
        """Get all keys matching pattern."""
        if not self.is_available():
            return []

        try:
            return self.client.keys(pattern)
        except:
            return []

    # ==========================================
    # HASH OPERATIONS
    # ==========================================

    def hset(self, name: str, key: str, value: Any) -> bool:
        """Set hash field."""
        if not self.is_available():
            return False

        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            self.client.hset(name, key, value)
            return True
        except:
            return False

    def hget(self, name: str, key: str) -> Optional[Any]:
        """Get hash field."""
        if not self.is_available():
            return None

        try:
            value = self.client.hget(name, key)
            if value:
                try:
                    return json.loads(value)
                except:
                    return value
            return None
        except:
            return None

    def hgetall(self, name: str) -> dict:
        """Get all hash fields."""
        if not self.is_available():
            return {}

        try:
            return self.client.hgetall(name)
        except:
            return {}

    # ==========================================
    # LIST OPERATIONS
    # ==========================================

    def lpush(self, key: str, *values: Any) -> bool:
        """Push to list (left)."""
        if not self.is_available():
            return False

        try:
            serialized = [json.dumps(v) if isinstance(v, (dict, list)) else str(v) for v in values]
            self.client.lpush(key, *serialized)
            return True
        except:
            return False

    def rpush(self, key: str, *values: Any) -> bool:
        """Push to list (right)."""
        if not self.is_available():
            return False

        try:
            serialized = [json.dumps(v) if isinstance(v, (dict, list)) else str(v) for v in values]
            self.client.rpush(key, *serialized)
            return True
        except:
            return False

    def lrange(self, key: str, start: int = 0, end: int = -1) -> list:
        """Get list range."""
        if not self.is_available():
            return []

        try:
            values = self.client.lrange(key, start, end)
            return [
                json.loads(v) if v.startswith(('[', '{')) else v
                for v in values
            ]
        except:
            return []

    # ==========================================
    # SET OPERATIONS
    # ==========================================

    def sadd(self, key: str, *members: Any) -> bool:
        """Add to set."""
        if not self.is_available():
            return False

        try:
            self.client.sadd(key, *members)
            return True
        except:
            return False

    def sismember(self, key: str, member: Any) -> bool:
        """Check if member in set."""
        if not self.is_available():
            return False

        try:
            return bool(self.client.sismember(key, member))
        except:
            return False

    def smembers(self, key: str) -> set:
        """Get all set members."""
        if not self.is_available():
            return set()

        try:
            return self.client.smembers(key)
        except:
            return set()


# Singleton instance
cache = RedisCache()


# ============================================
# DECORATORS
# ============================================

def cached(ttl: int = 3600, key_prefix: str = ""):
    """
    Cache decorator for functions.

    Usage:
        @cached(ttl=300, key_prefix="user")
        def get_user(user_id: str):
            return db.query(User).get(user_id)
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            key_parts = [key_prefix, func.__name__]
            key_parts.extend([str(arg) for arg in args])
            key_parts.extend([f"{k}={v}" for k, v in sorted(kwargs.items())])
            cache_key = ":".join(filter(None, key_parts))

            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Execute function
            result = func(*args, **kwargs)

            # Store in cache
            cache.set(cache_key, result, ttl)

            return result

        return wrapper

    return decorator


def invalidate_cache(pattern: str):
    """
    Invalidate cache decorator.

    Usage:
        @invalidate_cache("user:*")
        def update_user(user_id: str):
            ...
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            cache.delete_pattern(pattern)
            return result

        return wrapper

    return decorator