"""Base class for the in-memory session managers."""

import secrets
from collections.abc import Hashable
from datetime import timedelta

from spb.classes.cache import Cache
from spb.classes.interfaces import Session, TouchableSession

HASH_BYTES = 12
CACHE_MAX_SIZE = 10_000


class AbstractSessionManager[V: Session]:
    """Base class for all in-memory session managers.

    Provides what every session manager needs: opaque hash generation, a bounded expiring cache,
    and a find() method that refreshes the expiry of touchable sessions on access. Subclasses focus
    only on their domain-specific creation, mutation and consumption logic.
    """

    def __init__(self, expiry: timedelta) -> None:
        self._cache: Cache[str, V] = Cache(CACHE_MAX_SIZE, expiry)

    def find(self, hash: str) -> V | None:
        """Looks up an active session by hash, refreshing its expiry when it is touchable.

        Returns:
            The session, or None when it is absent or expired.
        """
        session = self._cache.get_if_present(hash)
        if isinstance(session, TouchableSession):
            session = session.touch()
            self._cache.put(hash, session)
        return session

    def invalidate(self, hash: str) -> None:
        """Removes a session."""
        self._cache.remove(hash)

    def _store(self, session: V) -> V:
        self._cache.put(session.hash, session)
        return session

    def _evict(self, session: V) -> None:
        self._cache.remove(session.hash)

    @staticmethod
    def _generate_hash() -> str:
        """Returns a new opaque, URL-safe session hash."""
        return secrets.token_urlsafe(HASH_BYTES)


class ReverseIndex[K: Hashable]:
    """A mapping from a secondary lookup key to a session hash.

    Used by session managers that resolve sessions by a key other than the hash, such as a prompt
    message id or a channel verification code. The entries expire alongside the sessions.
    """

    def __init__(self, expiry: timedelta) -> None:
        self._index: Cache[K, str] = Cache(CACHE_MAX_SIZE, expiry)

    def store(self, key: K, hash: str) -> None:
        self._index.put(key, hash)

    def clear(self, key: K | None) -> None:
        if key is not None:
            self._index.remove(key)

    def resolve(self, key: K) -> str | None:
        return self._index.get_if_present(key)
