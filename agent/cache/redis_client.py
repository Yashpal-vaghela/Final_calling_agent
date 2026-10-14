"""
Redis L2 Cache Client — Topology C (Local RAM L1 + Redis Cloud L2)

Provides a single async Redis connection pool for the calling agent.
All operations gracefully degrade to None on connection failure.
Never raises exceptions to calling code.
"""
import os
import json
import asyncio
import logging
from typing import Any, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Module-level singleton — initialized once
_redis_client = None
_redis_loop = None
_redis_enabled = os.getenv("REDIS_ENABLED", "false").lower() in ("1", "true", "yes")
_redis_url = os.getenv("REDIS_URL", "")
_connect_timeout = float(os.getenv("REDIS_CONNECT_TIMEOUT", "2"))


async def get_redis():
    """Returns the singleton async Redis client, or None if disabled/unavailable."""
    global _redis_client, _redis_loop
    if not _redis_enabled or not _redis_url:
        return None

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _redis_client is not None:
        if _redis_loop is not None and (_redis_loop.is_closed() or _redis_loop is not current_loop):
            _redis_client = None
            _redis_loop = None
        else:
            return _redis_client

    try:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            _redis_url,
            socket_connect_timeout=_connect_timeout,
            socket_timeout=_connect_timeout,
            decode_responses=True,
        )
        _redis_loop = current_loop
        await _redis_client.ping()
        logger.info("[Redis] Connected to Redis L2 cache.")
    except Exception as e:
        logger.warning(f"[Redis] Could not connect: {e}. Running without cache.")
        _redis_client = None
        _redis_loop = None
    return _redis_client


async def close_redis() -> None:
    """Closes the singleton Redis connection."""
    global _redis_client, _redis_loop
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
        _redis_client = None
        _redis_loop = None


async def cache_get(key: str) -> Optional[Any]:
    """Gets a value from Redis. Returns None on miss or error."""
    try:
        client = await get_redis()
        if client is None:
            return None
        raw = await client.get(key)
        if raw is None:
            logger.info(f"[Redis L2 CACHE MISS] key='{key}'")
            return None
        if isinstance(raw, (str, bytes, bytearray)):
            logger.info(f"[Redis L2 CACHE HIT] key='{key}'")
            return json.loads(raw)
        return None
    except Exception as e:
        logger.warning(f"[Redis] cache_get({key}) failed: {e}")
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int = 3600) -> bool:
    """Sets a value in Redis with TTL. Returns False on error."""
    try:
        client = await get_redis()
        if client is None:
            return False
        await client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
        logger.info(f"[Redis L2 CACHE SET] key='{key}' ttl={ttl_seconds}s")
        return True
    except Exception as e:
        logger.warning(f"[Redis] cache_set({key}) failed: {e}")
        return False


async def cache_delete(key: str) -> bool:
    """Deletes a key from Redis. Used for call context cleanup on hangup."""
    try:
        client = await get_redis()
        if client is None:
            return False
        await client.delete(key)
        logger.info(f"[Redis L2 CACHE DELETE] key='{key}'")
        return True
    except Exception as e:
        logger.warning(f"[Redis] cache_delete({key}) failed: {e}")
        return False


def cache_get_sync(key: str) -> Optional[Any]:
    """Synchronous version for use in sync code paths (caller_context lookup)."""
    if not _redis_enabled or not _redis_url:
        return None
    try:
        import redis as sync_redis
        client = sync_redis.from_url(
            _redis_url,
            socket_connect_timeout=_connect_timeout,
            socket_timeout=_connect_timeout,
            decode_responses=True,
        )
        raw = client.get(key)
        client.close()
        if raw is None:
            logger.info(f"[Redis L2 CACHE MISS] (sync) key='{key}'")
            return None
        if isinstance(raw, (str, bytes, bytearray)):
            logger.info(f"[Redis L2 CACHE HIT] (sync) key='{key}'")
            return json.loads(raw)
        return None
    except Exception as e:
        logger.warning(f"[Redis] cache_get_sync({key}) failed: {e}")
        return None


def cache_set_sync(key: str, value: Any, ttl_seconds: int = 3600) -> bool:
    """Synchronous version of cache_set."""
    if not _redis_enabled or not _redis_url:
        return False
    try:
        import redis as sync_redis
        client = sync_redis.from_url(
            _redis_url,
            socket_connect_timeout=_connect_timeout,
            socket_timeout=_connect_timeout,
            decode_responses=True,
        )
        client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
        client.close()
        logger.info(f"[Redis L2 CACHE SET] (sync) key='{key}' ttl={ttl_seconds}s")
        return True
    except Exception as e:
        logger.warning(f"[Redis] cache_set_sync({key}) failed: {e}")
        return False


def cache_delete_sync(key: str) -> bool:
    """Synchronous version of cache_delete."""
    if not _redis_enabled or not _redis_url:
        return False
    try:
        import redis as sync_redis
        client = sync_redis.from_url(
            _redis_url,
            socket_connect_timeout=_connect_timeout,
            socket_timeout=_connect_timeout,
            decode_responses=True,
        )
        client.delete(key)
        client.close()
        logger.info(f"[Redis L2 CACHE DELETE] (sync) key='{key}'")
        return True
    except Exception as e:
        logger.warning(f"[Redis] cache_delete_sync({key}) failed: {e}")
        return False


# Re-export Phase 2 Data Structure helpers and TTL constants
from agent.cache.keys import (
    TTL_CALL_CONTEXT,
    TTL_FAQ,
    TTL_DENTISTS,
    TTL_CITY,
    TTL_STATE,
    TTL_CHECK_DENTIST,
    build_call_context_key,
    build_faq_key,
    build_dentists_key,
    build_city_key,
    build_state_key,
    build_dentist_key,
)
