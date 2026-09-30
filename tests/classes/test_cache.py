import asyncio
from datetime import timedelta

import pytest

from spb.classes.cache import Cache, LoadFailedException


def new_cache() -> Cache[int, str]:
    return Cache(100, timedelta(minutes=10))


def test_failed_load_is_not_cached() -> None:
    cache = new_cache()
    loads: list[int] = []

    def failing(key: int) -> str | None:
        loads.append(key)
        raise LoadFailedException("source down")

    def loading(key: int) -> str | None:
        loads.append(key)
        return "loaded"

    assert cache.get(1, failing, "fallback") == "fallback"
    assert cache.get(1, loading, "fallback") == "loaded"  # the failure must not be remembered
    assert len(loads) == 2


def test_failed_load_without_fallback_raises() -> None:
    def failing(key: int) -> str | None:
        raise LoadFailedException("source down")

    with pytest.raises(LoadFailedException):
        new_cache().get(1, failing)


def test_absence_is_cached() -> None:
    cache = new_cache()
    loads: list[int] = []

    def absent(key: int) -> str | None:
        loads.append(key)
        return None

    def loading(key: int) -> str | None:
        loads.append(key)
        return "loaded"

    assert cache.get(1, absent, "fallback") is None
    assert cache.get(1, loading, "fallback") is None  # a genuine absence is served from the cache
    assert len(loads) == 1


def test_put_none_removes_a_cached_absence() -> None:
    cache = new_cache()
    cache.get(1, lambda key: None)

    cache.put(1, None)

    assert cache.get(1, lambda key: "loaded") == "loaded"


def test_remove_if_removes_matching_keys() -> None:
    cache: Cache[str, str] = Cache(100, timedelta(minutes=10))
    cache.put("claim:1", "a")
    cache.put("claim:2", "b")
    cache.put("other", "c")

    cache.remove_if(lambda key: key.startswith("claim:"))

    assert cache.get_if_present("claim:1") is None
    assert cache.get_if_present("claim:2") is None
    assert cache.get_if_present("other") == "c"


@pytest.mark.anyio
async def test_concurrent_async_loads_share_one_load() -> None:
    cache = new_cache()
    loads: list[int] = []
    release = asyncio.Event()

    async def loader(key: int) -> str | None:
        loads.append(key)
        await release.wait()
        return "loaded"

    first = asyncio.create_task(cache.get_async(1, loader))
    second = asyncio.create_task(cache.get_async(1, loader))
    await asyncio.sleep(0)
    release.set()

    assert await first == "loaded"
    assert await second == "loaded"
    assert loads == [1]


@pytest.mark.anyio
async def test_failed_async_load_returns_the_fallback() -> None:
    cache = new_cache()

    async def failing(key: int) -> str | None:
        raise LoadFailedException("source down")

    async def loading(key: int) -> str | None:
        return "loaded"

    assert await cache.get_async(1, failing, "fallback") == "fallback"
    assert await cache.get_async(1, loading, "fallback") == "loaded"
