"""Tests for concurrent update dispatch, handler isolation, and graceful shutdown."""

import asyncio

import pytest

from spb.classes.handler import Handler
from spb.classes.handler_registry import HandlerRegistry
from spb.classes.update_dispatcher import UpdateDispatcher
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import updates

pytestmark = pytest.mark.anyio


def dispatcher(
    template: HandlerContext, worker_tasks: int, queue_capacity: int, *handlers: Handler
) -> UpdateDispatcher:
    return UpdateDispatcher(HandlerRegistry(handlers), template, worker_tasks, queue_capacity)


@update_handler(UpdateType.MESSAGE)
class CountingHandler(Handler):
    """Counts the messages it handles."""

    def __init__(self) -> None:
        self.count = 0

    async def handle(self, context: HandlerContext) -> None:
        self.count += 1


@update_handler(UpdateType.MESSAGE, priority=-1)
class SecondCountingHandler(CountingHandler):
    """A second message handler, to prove only one routed handler runs."""


@update_handler(UpdateType.COMMAND)
class CommandOnlyHandler(Handler):
    """Serves commands only, so other updates go unclaimed."""

    async def handle(self, context: HandlerContext) -> None:
        pass


@update_handler(UpdateType.ANY, mode=DispatchMode.OBSERVE)
class RecordingObserver(Handler):
    """Records that it ran before the routed handler."""

    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def handle(self, context: HandlerContext) -> None:
        self.order.append("observer")


@update_handler(UpdateType.MESSAGE)
class RecordingRouted(Handler):
    """Records that it ran after the observers."""

    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def handle(self, context: HandlerContext) -> None:
        self.order.append("routed")


@update_handler(UpdateType.ANY, mode=DispatchMode.OBSERVE)
class FailingObserver(Handler):
    """Always raises, to prove failures stay contained."""

    async def handle(self, context: HandlerContext) -> None:
        raise RuntimeError("deliberate failure")


@update_handler(UpdateType.MESSAGE)
class BlockingHandler(Handler):
    """Holds every update until released, recording the worker tasks it ran on."""

    def __init__(self, expected: int) -> None:
        self.tasks: set[str] = set()
        self.expected = expected
        self.arrived = asyncio.Event()
        self.released = asyncio.Event()

    async def handle(self, context: HandlerContext) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task.get_name())
        if len(self.tasks) == self.expected:
            self.arrived.set()
        await self.released.wait()


@update_handler(UpdateType.MESSAGE)
class SlowHandler(Handler):
    """Takes a moment over each update, so a shutdown has something left to drain."""

    def __init__(self) -> None:
        self.count = 0

    async def handle(self, context: HandlerContext) -> None:
        await asyncio.sleep(0.005)
        self.count += 1


@update_handler(UpdateType.COMMAND)
class CollectingHandler(Handler):
    """Records the update id and payload it was given, to prove contexts are not shared."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def handle(self, context: HandlerContext) -> None:
        assert context.update is not None
        assert context.command_name is not None
        await asyncio.sleep(0)
        self.seen.append(f"{context.update.update_id}/cmd{context.command_name[len('cmd') :]}")


async def test_every_submitted_update_reaches_a_handler(template: HandlerContext) -> None:
    counter = CountingHandler()
    dispatching = dispatcher(template, 4, 512, counter)

    for index in range(1, 201):
        assert dispatching.submit(updates.private_message(index, f"message {index}"))
    await dispatching.drain(10)

    assert counter.count == 200


async def test_observers_run_before_the_routed_handler(template: HandlerContext) -> None:
    order: list[str] = []
    dispatching = dispatcher(template, 1, 16, RecordingRouted(order), RecordingObserver(order))

    dispatching.submit(updates.private_message(1, "hello"))
    await dispatching.drain(10)

    assert order == ["observer", "routed"]


async def test_only_the_first_matching_routed_handler_runs(template: HandlerContext) -> None:
    first, second = CountingHandler(), SecondCountingHandler()
    dispatching = dispatcher(template, 1, 16, first, second)

    dispatching.submit(updates.private_message(1, "hello"))
    await dispatching.drain(10)

    assert first.count + second.count == 1


async def test_an_update_no_handler_claims_is_still_processed(template: HandlerContext) -> None:
    dispatching = dispatcher(template, 1, 16, CommandOnlyHandler())

    dispatching.submit(updates.business_connection(1, True))
    await dispatching.drain(10)

    assert dispatching.processed_updates == 1


async def test_a_failing_handler_does_not_stop_the_others(template: HandlerContext) -> None:
    counter = CountingHandler()
    dispatching = dispatcher(template, 1, 16, counter, FailingObserver())

    dispatching.submit(updates.private_message(1, "one"))
    dispatching.submit(updates.private_message(2, "two"))
    await dispatching.drain(10)

    assert counter.count == 2
    assert dispatching.processed_updates == 2


async def test_updates_are_processed_on_several_workers_at_once(template: HandlerContext) -> None:
    workers = 4
    handler = BlockingHandler(workers)
    dispatching = dispatcher(template, workers, 64, handler)

    for index in range(1, workers + 1):
        dispatching.submit(updates.private_message(index, f"concurrent {index}"))

    # Every update is held until all of them are in flight, so the event can only be set when the
    # dispatcher really is running them concurrently.
    await asyncio.wait_for(handler.arrived.wait(), 10)
    handler.released.set()
    await dispatching.drain(10)

    assert len(handler.tasks) == workers


async def test_handlers_see_the_update_bound_to_their_own_context(
    template: HandlerContext,
) -> None:
    handler = CollectingHandler()
    dispatching = dispatcher(template, 4, 256, handler)

    for index in range(1, 101):
        dispatching.submit(updates.private_message(index, f"/cmd{index}"))
    await dispatching.drain(10)

    assert len(handler.seen) == 100
    for entry in handler.seen:
        update_id, payload = entry.split("/cmd")
        assert update_id == payload


async def test_queued_updates_are_finished_rather_than_discarded(
    template: HandlerContext,
) -> None:
    handler = SlowHandler()
    dispatching = dispatcher(template, 2, 256, handler)

    for index in range(1, 51):
        dispatching.submit(updates.private_message(index, f"queued {index}"))

    assert await dispatching.drain(20)
    assert handler.count == 50
    assert dispatching.processed_updates == 50


async def test_no_update_is_accepted_once_shutdown_has_begun(template: HandlerContext) -> None:
    counter = CountingHandler()
    dispatching = dispatcher(template, 1, 16, counter)

    await dispatching.drain(10)

    assert not dispatching.is_accepting
    assert not dispatching.submit(updates.private_message(1, "late"))
    assert dispatching.dropped_updates == 1
    assert counter.count == 0


async def test_draining_twice_is_harmless(template: HandlerContext) -> None:
    dispatching = dispatcher(template, 1, 16, CountingHandler())

    assert await dispatching.drain(10)
    assert await dispatching.drain(10)
    assert not dispatching.is_accepting


async def test_updates_beyond_the_queue_capacity_are_dropped(template: HandlerContext) -> None:
    handler = BlockingHandler(1)
    dispatching = dispatcher(template, 1, 1, handler)

    dispatching.submit(updates.private_message(1, "in flight"))
    await asyncio.wait_for(handler.arrived.wait(), 10)

    assert dispatching.submit(updates.private_message(2, "queued"))
    assert not dispatching.submit(updates.private_message(3, "dropped"))
    assert dispatching.dropped_updates == 1

    handler.released.set()
    await dispatching.drain(10)


async def test_work_left_after_the_timeout_is_cancelled(template: HandlerContext) -> None:
    handler = BlockingHandler(1)
    dispatching = dispatcher(template, 1, 16, handler)

    dispatching.submit(updates.private_message(1, "stuck"))
    await asyncio.wait_for(handler.arrived.wait(), 10)

    assert not await dispatching.drain(0.01)
    assert dispatching.processed_updates == 1
