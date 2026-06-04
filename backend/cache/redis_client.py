import json
import logging
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from backend.config import get_settings


class RedisCache:
    def __init__(self) -> None:
        settings = get_settings()
        self._logger = logging.getLogger(__name__)
        # Support ACL username + URL and keep connection failures fast.
        conn_kwargs: dict[str, Any] = {
            "decode_responses": True,
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
            "retry_on_timeout": False,
        }

        redis_url = settings.redis_url
        if settings.redis_username:
            parts = urlsplit(redis_url)
            netloc = parts.hostname or ""
            if parts.port:
                netloc = f"{netloc}:{parts.port}"
            if parts.password:
                netloc = f"{settings.redis_username}:{parts.password}@{netloc}"
            else:
                netloc = f"{settings.redis_username}@{netloc}"
            redis_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

        self._client = Redis.from_url(redis_url, **conn_kwargs)
        if settings.redis_key_prefix:
            self._prefix = settings.redis_key_prefix
        elif settings.redis_username:
            self._prefix = f"{settings.redis_username}:"
        else:
            self._prefix = ""
        self._available = True

    @property
    def client(self) -> Redis:
        return self._client

    def disable(self) -> None:
        self._available = False

    def _apply_prefix(self, key: str) -> str:
        if not self._prefix:
            return key
        if key.startswith(self._prefix):
            return key
        return f"{self._prefix}{key}"

    def get_json(self, key: str) -> dict[str, Any] | list[Any] | None:
        if not self._available:
            return None
        try:
            raw = self._client.get(self._apply_prefix(key))
        except RedisError as exc:
            self._available = False
            self._logger.warning("Redis get failed; disabling cache for this process: %s", exc)
            return None
        if not raw:
            return None
        return json.loads(raw)

    def set_json(self, key: str, value: dict[str, Any] | list[Any], ttl_seconds: int | None = None) -> None:
        if not self._available:
            return
        payload = json.dumps(value)
        prefixed = self._apply_prefix(key)
        try:
            if ttl_seconds:
                self._client.setex(prefixed, ttl_seconds, payload)
                return
            self._client.set(prefixed, payload)
        except RedisError as exc:
            self._available = False
            self._logger.warning("Redis set failed; disabling cache for this process: %s", exc)

    def delete(self, key: str) -> None:
        """Delete a key from Redis safely. If Redis is unavailable or the delete
        fails, disable the cache for this process and log a warning instead of
        raising an exception to callers.
        """
        if not self._available:
            return
        prefixed = self._apply_prefix(key)
        try:
            self._client.delete(prefixed)
        except RedisError as exc:
            self._available = False
            self._logger.warning("Redis delete failed; disabling cache for this process: %s", exc)

    def increment_with_ttl(self, key: str, ttl_seconds: int) -> int | None:
        """Increment a key and ensure it expires.

        Returns the incremented count, or None when Redis is unavailable.
        """
        if not self._available:
            return None
        prefixed = self._apply_prefix(key)
        try:
            count = int(self._client.incr(prefixed))
            if count == 1:
                self._client.expire(prefixed, max(int(ttl_seconds), 1))
            return count
        except RedisError as exc:
            self._available = False
            self._logger.warning("Redis increment failed; disabling cache for this process: %s", exc)
            return None

    def ttl_seconds(self, key: str) -> int | None:
        """Return remaining TTL in seconds for a key, or None when unavailable."""
        if not self._available:
            return None
        prefixed = self._apply_prefix(key)
        try:
            ttl = int(self._client.ttl(prefixed))
            if ttl < 0:
                return None
            return ttl
        except RedisError as exc:
            self._available = False
            self._logger.warning("Redis ttl failed; disabling cache for this process: %s", exc)
            return None

    def get_int(self, key: str) -> int | None:
        """Return an integer value for a key, or None when unavailable."""
        if not self._available:
            return None
        prefixed = self._apply_prefix(key)
        try:
            raw = self._client.get(prefixed)
            if raw is None:
                return None
            return int(raw)
        except RedisError as exc:
            self._available = False
            self._logger.warning("Redis get_int failed; disabling cache for this process: %s", exc)
            return None
        except (TypeError, ValueError):
            return None


redis_cache = RedisCache()
