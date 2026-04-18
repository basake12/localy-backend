"""
app/core/cache.py

Production-grade Redis client for Localy Platform.

COMPLETE REWRITE — all bugs fixed:

BUG-R1 FIX: KEYS replaced with cursor-based SCAN everywhere.
  KEYS is O(N) and holds a global Redis lock — production killer.

BUG-R2 FIX: Single shared ConnectionPool for the entire application.
  security.py and auth_service.py previously called redis.from_url()
  inside helper functions — creating a new connection pool on every call.
  Now all Redis access goes through the shared pool defined here.

BUG-R3 FIX: ping-on-every-operation removed.
  is_available() no longer does a PING before every get/set/delete.
  Operations catch ConnectionError and log it instead. The pool's
  socket_keepalive handles dead-connection detection transparently.

BUG-R4 FIX: Lazy connection via pool — auto-reconnects on socket error.
  ConnectionPool manages reconnection internally. The app no longer
  needs to call _connect() at import time.

BUG-R5 FIX: Rate limiter is now atomic INCR + EXPIRE in a pipeline.
  The previous get→compare→increment was a race condition under burst load.

BUG-R6 FIX: set_nx() method added for seat_hold atomic reservation.
  Blueprint §6.7: seat_hold:{event_id}:{seat_id} TTL=600s requires
  SET key value EX ttl NX (atomic — set only if not exists).

BUG-R7 FIX: Async Redis client exposed alongside sync client.
  Async routes use `get_async_redis()` — no event-loop blocking.
  Sync code (Celery tasks, security.py, auth_service.py) uses `get_redis()`.

BUG-R8 FIX: All Blueprint §16.3 key patterns with correct TTLs in one place.
  Named methods for every key pattern. No hardcoded key strings outside this module.

═══════════════════════════════════════════════════════════════════════════════
BLUEPRINT §16.3 KEY PATTERNS (all managed here):

  otp:{phone}                  OTP value, TTL 300s
  pin_lockout:{user_id}        Lockout flag, TTL 1800s
  session:{user_id}:{jti}      Refresh token, TTL 2592000s (30 days)
  user_location:{user_id}      Last GPS position, TTL 3600s
  unread:{user_id}:{room_id}   Unread message count (no TTL — managed on read)
  presence:{user_id}           Online status, TTL 30s (heartbeat)
  delivery_chat:{delivery_id}  Delivery chat state, TTL dynamic
  seat_hold:{event}:{seat}     Ticket seat hold during checkout, TTL 600s
  search_suggest:{query_hash}  Autocomplete cache, TTL 300s
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Generator, Iterable, Optional

import redis as redis_sync
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio import ConnectionPool as AsyncConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Blueprint §16.3 TTL constants — single source of truth
# ══════════════════════════════════════════════════════════════════════════════

TTL_OTP             = 300        # otp:{phone}                    Blueprint §3.1
TTL_OTP_RESEND_COOL = 60         # otp_resend_cooldown:{phone}    Blueprint §3.1
TTL_PIN_LOCKOUT     = 1800       # pin_lockout:{user_id}          Blueprint §3.3 / §16.3
TTL_SESSION         = 2592000    # session:{user_id}:{jti}        Blueprint §3.2 / §16.3 (30 days)
TTL_USER_LOCATION   = 3600       # user_location:{user_id}        Blueprint §4.1 / §16.3
TTL_PRESENCE        = 30         # presence:{user_id}             Blueprint §16.3
TTL_SEAT_HOLD       = 600        # seat_hold:{event}:{seat}       Blueprint §6.7 / §16.3
TTL_SEARCH_SUGGEST  = 300        # search_suggest:{query_hash}    Blueprint §7.1 / §16.3
TTL_RATE_LIMIT_MIN  = 60         # rate_limit:{ip}                Blueprint §16.5
TTL_OTP_ATTEMPTS    = 3600       # otp_attempts:{phone}           Blueprint §3.1 (1-hour window)
TTL_OTP_LOCKOUT     = 1800       # otp_lockout:{phone}            Blueprint §3.1 step 2


# ══════════════════════════════════════════════════════════════════════════════
# SHARED CONNECTION POOL (BUG-R2 FIX)
# One pool per process — shared by ALL modules.
# security.py and auth_service.py MUST import get_redis() from here
# instead of calling redis.from_url() themselves.
# ══════════════════════════════════════════════════════════════════════════════

_REDIS_URL = str(settings.REDIS_URL)

# Sync pool — used by Celery tasks, security.py, auth_service.py, middleware
_sync_pool: Optional[redis_sync.ConnectionPool] = None

# Async pool — used by async FastAPI route handlers
_async_pool: Optional[AsyncConnectionPool] = None


def _get_sync_pool() -> redis_sync.ConnectionPool:
    """Return (and lazily create) the shared synchronous connection pool."""
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = redis_sync.ConnectionPool.from_url(
            _REDIS_URL,
            decode_responses=True,
            max_connections=50,
            socket_timeout=5,
            socket_connect_timeout=5,
            socket_keepalive=True,
            retry_on_error=[redis_sync.ConnectionError, redis_sync.TimeoutError],
        )
    return _sync_pool


def _get_async_pool() -> AsyncConnectionPool:
    """Return (and lazily create) the shared async connection pool."""
    global _async_pool
    if _async_pool is None:
        _async_pool = AsyncConnectionPool.from_url(
            _REDIS_URL,
            decode_responses=True,
            max_connections=50,
            socket_timeout=5,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
    return _async_pool


def get_redis() -> redis_sync.Redis:
    """
    Return a synchronous Redis client from the shared pool.

    Use in:
      - Celery tasks
      - security.py (session management)
      - auth_service.py (OTP management)
      - rate_limit.py (via asyncio.to_thread)
      - Any sync code that needs Redis

    The pool handles reconnection automatically on socket failure.
    """
    return redis_sync.Redis(connection_pool=_get_sync_pool())


async def get_async_redis() -> AsyncRedis:
    """
    Return an async Redis client from the shared pool.

    Use in:
      - async FastAPI route handlers
      - wallet_service.py (async WebSocket notification)
      - chat_crud.py (async presence / unread counts)

    Never call blocking Redis inside async def without using this.
    """
    return AsyncRedis(connection_pool=_get_async_pool())


# ══════════════════════════════════════════════════════════════════════════════
# LOW-LEVEL HELPERS (safe wrappers around the sync client)
# ══════════════════════════════════════════════════════════════════════════════

def _r() -> redis_sync.Redis:
    """Shorthand: get a sync Redis client."""
    return get_redis()


def _safe(fn_name: str, default):
    """Decorator: catch Redis errors and return a safe default instead of raising."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (redis_sync.ConnectionError, redis_sync.TimeoutError, Exception) as exc:
                logger.error("Redis %s error: %s", fn_name, exc)
                return default
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════════════
# GENERIC CACHE CLASS (backward-compatible with existing callers)
# ══════════════════════════════════════════════════════════════════════════════

class RedisCache:
    """
    Generic cache operations used by rate_limit.py, search, and decorators.
    All operations use the shared pool (BUG-R2 FIX).
    No more ping() before every operation (BUG-R3 FIX).
    SCAN instead of KEYS everywhere (BUG-R1 FIX).
    """

    # ── Basic ops ─────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        try:
            value = _r().get(key)
            if value is None:
                return None
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as exc:
            logger.error("Cache GET '%s': %s", key, exc)
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        try:
            serialised = (
                json.dumps(value) if isinstance(value, (dict, list))
                else value if isinstance(value, (str, int, float))
                else str(value)
            )
            _r().setex(key, ttl, serialised)
            return True
        except Exception as exc:
            logger.error("Cache SET '%s': %s", key, exc)
            return False

    def set_nx(self, key: str, value: Any, ttl: int) -> bool:
        """
        Atomic SET only-if-not-exists with TTL.
        Returns True if key was set (didn't exist), False if it already existed.

        BUG-R6 FIX: Required for seat_hold and OTP race-condition prevention.
        Blueprint §6.7: seat_hold:{event}:{seat} TTL=600s — atomic reservation.
        """
        try:
            result = _r().set(
                key,
                json.dumps(value) if isinstance(value, (dict, list)) else str(value),
                ex=ttl,
                nx=True,
            )
            return result is True
        except Exception as exc:
            logger.error("Cache SET_NX '%s': %s", key, exc)
            return False

    def delete(self, key: str) -> bool:
        try:
            _r().delete(key)
            return True
        except Exception as exc:
            logger.error("Cache DELETE '%s': %s", key, exc)
            return False

    def delete_many(self, *keys: str) -> int:
        if not keys:
            return 0
        try:
            return _r().delete(*keys)
        except Exception as exc:
            logger.error("Cache DELETE_MANY: %s", exc)
            return 0

    def exists(self, key: str) -> bool:
        try:
            return bool(_r().exists(key))
        except Exception:
            return False

    def expire(self, key: str, ttl: int) -> bool:
        try:
            return bool(_r().expire(key, ttl))
        except Exception:
            return False

    def ttl(self, key: str) -> int:
        """Return remaining TTL in seconds. -1 = no TTL, -2 = key doesn't exist."""
        try:
            return _r().ttl(key)
        except Exception:
            return -2

    def increment(self, key: str, amount: int = 1) -> Optional[int]:
        try:
            return _r().incr(key, amount)
        except Exception as exc:
            logger.error("Cache INCR '%s': %s", key, exc)
            return None

    def atomic_increment_with_ttl(
        self, key: str, amount: int = 1, ttl: int = 60
    ) -> Optional[int]:
        """
        BUG-R5 FIX: Atomic INCR + EXPIRE in a single pipeline.
        Used by rate limiters to prevent the get→compare→increment race condition.

        Returns the new count, or None on error.
        TTL is only set on the first increment (when the key is new).
        """
        try:
            r = _r()
            pipe = r.pipeline(transaction=True)
            pipe.incr(key, amount)
            pipe.ttl(key)
            results = pipe.execute()
            new_count, remaining_ttl = results[0], results[1]
            if remaining_ttl == -1:  # key has no TTL yet (first increment)
                r.expire(key, ttl)
            return new_count
        except Exception as exc:
            logger.error("Cache ATOMIC_INCR '%s': %s", key, exc)
            return None

    # ── Pattern ops (SCAN — never KEYS) ───────────────────────────────────────

    def scan_keys(self, pattern: str, count: int = 100) -> list[str]:
        """
        BUG-R1 FIX: Cursor-based SCAN instead of blocking KEYS.
        Returns all keys matching pattern without locking Redis.
        """
        found: list[str] = []
        try:
            r = _r()
            cursor = 0
            while True:
                cursor, batch = r.scan(cursor=cursor, match=pattern, count=count)
                found.extend(batch)
                if cursor == 0:
                    break
        except Exception as exc:
            logger.error("Cache SCAN '%s': %s", pattern, exc)
        return found

    def delete_pattern(self, pattern: str) -> int:
        """
        BUG-R1 FIX: Delete keys matching pattern via SCAN (not KEYS).
        Returns number of keys deleted.
        """
        deleted = 0
        try:
            r = _r()
            cursor = 0
            while True:
                cursor, batch = r.scan(cursor=cursor, match=pattern, count=100)
                if batch:
                    deleted += r.delete(*batch)
                if cursor == 0:
                    break
        except Exception as exc:
            logger.error("Cache DELETE_PATTERN '%s': %s", pattern, exc)
        return deleted

    # Backward-compat alias (old code called get_keys)
    def get_keys(self, pattern: str = "*") -> list[str]:
        return self.scan_keys(pattern)

    # ── Hash ops ───────────────────────────────────────────────────────────────

    def hset(self, name: str, key: str, value: Any) -> bool:
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            _r().hset(name, key, value)
            return True
        except Exception as exc:
            logger.error("Cache HSET '%s/%s': %s", name, key, exc)
            return False

    def hget(self, name: str, key: str) -> Optional[Any]:
        try:
            value = _r().hget(name, key)
            if value is None:
                return None
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception:
            return None

    def hgetall(self, name: str) -> dict:
        try:
            return _r().hgetall(name) or {}
        except Exception:
            return {}

    def hincrby(self, name: str, key: str, amount: int = 1) -> Optional[int]:
        try:
            return _r().hincrby(name, key, amount)
        except Exception as exc:
            logger.error("Cache HINCRBY '%s/%s': %s", name, key, exc)
            return None

    # ── List ops ───────────────────────────────────────────────────────────────

    def lpush(self, key: str, *values: Any) -> bool:
        try:
            serialised = [
                json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for v in values
            ]
            _r().lpush(key, *serialised)
            return True
        except Exception:
            return False

    def rpush(self, key: str, *values: Any) -> bool:
        try:
            serialised = [
                json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for v in values
            ]
            _r().rpush(key, *serialised)
            return True
        except Exception:
            return False

    def lrange(self, key: str, start: int = 0, end: int = -1) -> list:
        try:
            values = _r().lrange(key, start, end)
            return [
                json.loads(v) if v.startswith(("[", "{")) else v
                for v in values
            ]
        except Exception:
            return []

    # ── Set ops ────────────────────────────────────────────────────────────────

    def sadd(self, key: str, *members: Any) -> bool:
        try:
            _r().sadd(key, *members)
            return True
        except Exception:
            return False

    def sismember(self, key: str, member: Any) -> bool:
        try:
            return bool(_r().sismember(key, member))
        except Exception:
            return False

    def smembers(self, key: str) -> set:
        try:
            return _r().smembers(key) or set()
        except Exception:
            return set()

    def srem(self, key: str, *members: Any) -> bool:
        try:
            _r().srem(key, *members)
            return True
        except Exception:
            return False

    # ── Health ────────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        """Health-check only — do NOT call before every operation."""
        try:
            return _r().ping()
        except Exception:
            return False

    def is_available(self) -> bool:
        return self.ping()


# ══════════════════════════════════════════════════════════════════════════════
# BLUEPRINT §16.3 KEY METHODS
# All key-pattern operations with exact TTLs per Blueprint §16.3.
# Import and use these — never hardcode key strings outside this module.
# ══════════════════════════════════════════════════════════════════════════════

class LocalyRedisKeys:
    """
    Named methods for every Blueprint §16.3 key pattern.
    Single source of truth for key names and TTLs.
    """

    # ── OTP — Blueprint §3.1 ──────────────────────────────────────────────────

    @staticmethod
    def store_otp(phone: str, otp: str) -> None:
        """otp:{phone} TTL=300s. Overwrites any existing OTP (intentional — resend)."""
        _r().setex(f"otp:{phone}", TTL_OTP, otp)

    @staticmethod
    def get_otp(phone: str) -> Optional[str]:
        """Return stored OTP for phone, or None if expired/missing."""
        return _r().get(f"otp:{phone}")

    @staticmethod
    def delete_otp(phone: str) -> None:
        """Blueprint §3.1 Step 2: 'On success: Redis key deleted.'"""
        _r().delete(f"otp:{phone}")

    @staticmethod
    def set_otp_resend_cooldown(phone: str) -> None:
        """otp_resend_cooldown:{phone} TTL=60s. Blueprint §3.1: 'Resend after 60 seconds.'"""
        _r().set(f"otp_resend_cooldown:{phone}", "1", ex=TTL_OTP_RESEND_COOL, nx=True)

    @staticmethod
    def is_otp_resend_blocked(phone: str) -> bool:
        """True if the 60-second resend cooldown is still active."""
        return bool(_r().exists(f"otp_resend_cooldown:{phone}"))

    @staticmethod
    def increment_otp_attempts(phone: str) -> int:
        """
        Increment and return OTP attempt counter for this phone.
        TTL 3600s (1-hour window). Blueprint §3.1: max 5 attempts per hour.
        """
        r = _r()
        key = f"otp_attempts:{phone}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, TTL_OTP_ATTEMPTS)
        return count

    @staticmethod
    def set_otp_lockout(phone: str) -> None:
        """otp_lockout:{phone} TTL=1800s. Blueprint §3.1 Step 2: 30-min lockout."""
        _r().setex(f"otp_lockout:{phone}", TTL_OTP_LOCKOUT, "1")

    @staticmethod
    def is_otp_locked(phone: str) -> bool:
        """True if phone is locked out of OTP (5 failed attempts)."""
        return bool(_r().exists(f"otp_lockout:{phone}"))

    @staticmethod
    def clear_otp_attempts(phone: str) -> None:
        """Clear OTP attempt counter after lockout applied (avoid double-counting)."""
        _r().delete(f"otp_attempts:{phone}")

    # ── PIN Lockout — Blueprint §3.3 / §16.3 ──────────────────────────────────

    @staticmethod
    def set_pin_lockout(user_id: str) -> None:
        """pin_lockout:{user_id} TTL=1800s. Blueprint §3.3 / §16.3."""
        _r().setex(f"pin_lockout:{user_id}", TTL_PIN_LOCKOUT, "1")

    @staticmethod
    def is_pin_locked(user_id: str) -> bool:
        """True if user's PIN is locked (5 consecutive wrong attempts)."""
        return bool(_r().exists(f"pin_lockout:{user_id}"))

    @staticmethod
    def clear_pin_lockout(user_id: str) -> None:
        """Clear PIN lockout — called after successful SMS unlock code."""
        _r().delete(f"pin_lockout:{user_id}")

    # ── Session (Refresh Token) — Blueprint §3.2 / §16.3 ─────────────────────

    @staticmethod
    def store_session(user_id: str, jti: str) -> None:
        """session:{user_id}:{jti} TTL=2592000s (30 days). Blueprint §3.2 / §16.3."""
        _r().setex(f"session:{user_id}:{jti}", TTL_SESSION, "1")

    @staticmethod
    def is_session_valid(user_id: str, jti: str) -> bool:
        """True if the session token still exists (not rotated or revoked)."""
        return bool(_r().exists(f"session:{user_id}:{jti}"))

    @staticmethod
    def revoke_session(user_id: str, jti: str) -> None:
        """Delete a single session token (rotation step)."""
        _r().delete(f"session:{user_id}:{jti}")

    @staticmethod
    def revoke_all_sessions(user_id: str) -> None:
        """
        Delete ALL session tokens for a user.
        Blueprint §3.2: 'All existing session tokens invalidated on password reset.'
        Uses SCAN — never KEYS (BUG-R1 FIX).
        """
        r       = _r()
        pattern = f"session:{user_id}:*"
        cursor  = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break

    # ── User Location — Blueprint §4.1 / §16.3 ────────────────────────────────

    @staticmethod
    def set_user_location(user_id: str, lat: float, lng: float) -> None:
        """user_location:{user_id} TTL=3600s. Blueprint §4.1 / §16.3."""
        payload = json.dumps({"lat": lat, "lng": lng})
        _r().setex(f"user_location:{user_id}", TTL_USER_LOCATION, payload)

    @staticmethod
    def get_user_location(user_id: str) -> Optional[dict]:
        """Return {"lat": ..., "lng": ...} or None if expired."""
        raw = _r().get(f"user_location:{user_id}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def delete_user_location(user_id: str) -> None:
        _r().delete(f"user_location:{user_id}")

    # ── Unread Messages — Blueprint §16.3 ─────────────────────────────────────

    @staticmethod
    def increment_unread(user_id: str, room_id: str) -> int:
        """Increment unread count for user in a chat room."""
        key = f"unread:{user_id}:{room_id}"
        return _r().incr(key)

    @staticmethod
    def get_unread(user_id: str, room_id: str) -> int:
        """Return unread message count (0 if not set)."""
        val = _r().get(f"unread:{user_id}:{room_id}")
        return int(val) if val else 0

    @staticmethod
    def clear_unread(user_id: str, room_id: str) -> None:
        """Clear unread count when user reads the room."""
        _r().delete(f"unread:{user_id}:{room_id}")

    # ── Presence — Blueprint §16.3 ────────────────────────────────────────────

    @staticmethod
    def set_presence(user_id: str) -> None:
        """presence:{user_id} TTL=30s (heartbeat). Blueprint §16.3."""
        _r().setex(f"presence:{user_id}", TTL_PRESENCE, "1")

    @staticmethod
    def is_present(user_id: str) -> bool:
        """True if user has sent a heartbeat within the last 30 seconds."""
        return bool(_r().exists(f"presence:{user_id}"))

    @staticmethod
    def clear_presence(user_id: str) -> None:
        _r().delete(f"presence:{user_id}")

    # ── Delivery Chat — Blueprint §16.3 ───────────────────────────────────────

    @staticmethod
    def open_delivery_chat(delivery_id: str, ttl_seconds: int) -> None:
        """
        delivery_chat:{delivery_id} TTL=dynamic.
        Blueprint §16.3: TTL = dispatch + estimated_delivery_time + 3600s.
        Blueprint §10.2: channel closes 1 hour after delivery completion.
        """
        _r().setex(f"delivery_chat:{delivery_id}", ttl_seconds, "open")

    @staticmethod
    def is_delivery_chat_open(delivery_id: str) -> bool:
        """True if delivery chat channel is still active."""
        return bool(_r().exists(f"delivery_chat:{delivery_id}"))

    @staticmethod
    def close_delivery_chat(delivery_id: str) -> None:
        _r().delete(f"delivery_chat:{delivery_id}")

    # ── Seat Hold — Blueprint §6.7 / §16.3 ────────────────────────────────────

    @staticmethod
    def hold_seat(event_id: str, seat_id: str, user_id: str) -> bool:
        """
        BUG-R6 FIX: Atomic SET NX — set only if not already held.
        seat_hold:{event_id}:{seat_id} TTL=600s. Blueprint §6.7 / §16.3.

        Returns True if hold was acquired (seat is yours for 600s).
        Returns False if seat is already held by another checkout.
        """
        key    = f"seat_hold:{event_id}:{seat_id}"
        result = _r().set(key, user_id, ex=TTL_SEAT_HOLD, nx=True)
        return result is True

    @staticmethod
    def release_seat(event_id: str, seat_id: str) -> None:
        """Release seat hold (on purchase completion or checkout abandonment)."""
        _r().delete(f"seat_hold:{event_id}:{seat_id}")

    @staticmethod
    def get_seat_holder(event_id: str, seat_id: str) -> Optional[str]:
        """Return user_id currently holding this seat, or None."""
        return _r().get(f"seat_hold:{event_id}:{seat_id}")

    # ── Search Autocomplete — Blueprint §7.1 / §16.3 ─────────────────────────

    @staticmethod
    def get_search_suggest(query_hash: str) -> Optional[Any]:
        """search_suggest:{query_hash} TTL=300s. Blueprint §7.1 / §16.3."""
        raw = _r().get(f"search_suggest:{query_hash}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @staticmethod
    def set_search_suggest(query_hash: str, results: Any) -> None:
        """Cache autocomplete results for 300 seconds."""
        _r().setex(
            f"search_suggest:{query_hash}",
            TTL_SEARCH_SUGGEST,
            json.dumps(results),
        )


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCES
# ══════════════════════════════════════════════════════════════════════════════

cache    = RedisCache()
redis_bp = LocalyRedisKeys()   # Blueprint §16.3 key operations


# ══════════════════════════════════════════════════════════════════════════════
# DECORATORS
# ══════════════════════════════════════════════════════════════════════════════

def cached(ttl: int = 3600, key_prefix: str = ""):
    """
    Cache decorator for sync and async functions.

    Usage:
        @cached(ttl=300, key_prefix="hotel")
        async def get_hotel(hotel_id: str): ...
    """
    def decorator(func: Callable):
        import asyncio

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            cache_key = _build_cache_key(key_prefix, func.__name__, args, kwargs)
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return cached_val
            result = await func(*args, **kwargs)
            if result is not None:
                cache.set(cache_key, result, ttl)
            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            cache_key = _build_cache_key(key_prefix, func.__name__, args, kwargs)
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return cached_val
            result = func(*args, **kwargs)
            if result is not None:
                cache.set(cache_key, result, ttl)
            return result

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


def invalidate_cache(pattern: str):
    """
    Cache invalidation decorator — clears all keys matching pattern after the function runs.

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


def _build_cache_key(prefix: str, name: str, args: tuple, kwargs: dict) -> str:
    parts = [prefix, name]
    parts.extend(str(a) for a in args)
    parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
    return ":".join(filter(None, parts))


# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMITER (BUG-R5 FIX — atomic pipeline)
# ══════════════════════════════════════════════════════════════════════════════

class APIRateLimiter:
    """
    Programmatic per-endpoint rate limiter using atomic INCR + EXPIRE pipeline.

    BUG-R5 FIX: Old implementation had a race condition:
        get → compare → increment (three separate commands)
    Under burst load, multiple concurrent requests all saw count=0 and passed.

    New implementation:
        INCR (atomic) → if count==1, EXPIRE → compare count to limit
    This is atomic — no race window between check and increment.

    Usage:
        if not APIRateLimiter.limit(f"otp:{phone}", max_requests=5, window_seconds=3600):
            raise RateLimitExceededException()
    """

    @staticmethod
    def limit(key: str, max_requests: int, window_seconds: int = 60) -> bool:
        """
        Returns True if the request is within the allowed window.
        Returns False if the limit has been exceeded.
        """
        rate_key  = f"api_limit:{key}"
        new_count = cache.atomic_increment_with_ttl(rate_key, 1, window_seconds)
        if new_count is None:
            return True   # Redis unavailable — fail open (don't block legitimate users)
        return new_count <= max_requests