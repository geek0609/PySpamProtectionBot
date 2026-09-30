"""In-memory cache used as the runtime information layer of the bot."""

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from datetime import timedelta
from typing import Any, cast

from cachetools import TTLCache

_ABSENT = object()
"""Stored for loaders that returned None, representing an explicit absence."""

_NO_FALLBACK: Any = object()


class LoadFailedException(Exception):
    """Raised by a loader to report that its source failed, as opposed to finding nothing.

    The key is left uncached, so the next lookup retries the load.
    """


class Cache[K: Hashable, V]:
    """Bounded, expiring in-memory cache used as the runtime information layer of the bot.

    Entries are evicted by size and after a fixed time since they were written, and lookups may
    load values on demand through a loader function. The event loop never switches tasks inside a
    synchronous loader, so a missing key is loaded at most once; get_async shares one in-flight
    load per key to keep that guarantee for loaders that await.

    Absent results are cached too: when a loader returns None, the key is retained as absent until
    it is written to, removed or expires, so repeated lookups of a missing row (an unconfigured
    chat, an unknown user, ...) do not hit the database again. The public methods translate that
    state back to None.
    """

    def __init__(self, maximum_size: int, expiry: timedelta) -> None:
        """
        Args:
            maximum_size: The number of entries kept before the least recently used are evicted.
            expiry: The time after which an entry is considered expired.
        """
        self._cache: TTLCache[K, object] = TTLCache(maximum_size, expiry.total_seconds())
        self._loading: dict[K, asyncio.Task[V | None]] = {}

    def get(
        self, key: K, loader: Callable[[K], V | None], fallback: V | None = _NO_FALLBACK
    ) -> V | None:
        """Returns the value for a key, loading and caching it when missing.

        Args:
            key: The key to look up.
            loader: Produces the value for a missing key, or None when there is none.
            fallback: Returned when the loader raises LoadFailedException; without it, the
                exception propagates.

        Returns:
            The cached or freshly loaded value, None when the loader found nothing, or the fallback
            when the loader failed.
        """
        if key in self._cache:
            return self._unwrap(self._cache[key])

        try:
            value = loader(key)
        except LoadFailedException:
            if fallback is _NO_FALLBACK:
                raise
            return fallback

        self._cache[key] = _ABSENT if value is None else value
        return value

    async def get_async(
        self,
        key: K,
        loader: Callable[[K], Awaitable[V | None]],
        fallback: V | None = _NO_FALLBACK,
    ) -> V | None:
        """Returns the value for a key like get, for a loader that awaits.

        Concurrent lookups of the same missing key share a single load, so its source is hit at
        most once per missing key.
        """
        if key in self._cache:
            return self._unwrap(self._cache[key])

        load = self._loading.get(key)
        if load is None:
            load = asyncio.create_task(self._load(key, loader))
            self._loading[key] = load
            load.add_done_callback(lambda _: self._loading.pop(key, None))

        try:
            return await asyncio.shield(load)
        except LoadFailedException:
            if fallback is _NO_FALLBACK:
                raise
            return fallback

    async def _load(self, key: K, loader: Callable[[K], Awaitable[V | None]]) -> V | None:
        value = await loader(key)
        self._cache[key] = _ABSENT if value is None else value
        return value

    def get_if_present(self, key: K) -> V | None:
        """Returns the value for a key without loading it, or None when it is not cached."""
        return self._unwrap(self._cache.get(key))

    def put(self, key: K, value: V | None) -> None:
        """Associates a value with a key, replacing any previous value or cached absence.

        Passing None removes the entry instead.
        """
        if value is None:
            self._cache.pop(key, None)
        else:
            self._cache[key] = value

    def remove(self, key: K) -> None:
        """Removes the entry for a key, if any."""
        self._cache.pop(key, None)

    def remove_if(self, predicate: Callable[[K], bool]) -> None:
        """Removes every entry whose key matches the predicate, such as all keys under a prefix."""
        for key in [key for key in self._cache if predicate(key)]:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Removes all entries."""
        self._cache.clear()

    def _unwrap(self, value: object) -> V | None:
        return None if value is _ABSENT or value is None else cast(V, value)
