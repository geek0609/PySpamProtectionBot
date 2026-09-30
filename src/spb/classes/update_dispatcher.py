"""Routes incoming updates to the registered handlers across a pool of worker tasks."""

import asyncio
import itertools
import logging

from pyrogram.types import Message

from spb.classes.handler import Handler
from spb.classes.handler_registry import HandlerRegistry
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from spb.objects.media_group_state import MediaGroupState
from spb.objects.update import Update
from spb.utilities import report_attachments

log = logging.getLogger(__name__)

MEDIA_GROUP_CACHE_PREFIX = "media-group:"
"""Cache key prefix for album state, scoped by chat."""

_worker_numbers = itertools.count(1)
"""Numbers the worker tasks, so their names are distinct across dispatchers."""


class UpdateDispatcher:
    """Routes incoming updates to the registered handlers across a pool of worker tasks.

    The Telegram connection only enqueues work here and never runs a handler itself, so a slow
    handler delays one update rather than the whole bot. Updates wait in a bounded queue; when the
    queue is full the surplus is dropped and logged, which keeps a burst from growing memory
    without limit.

    Each update is processed as: every matching observing handler runs first, in priority order,
    then the first matching routed handler claims the update. A handler that raises is logged and
    skipped, never retried, so one failure cannot stall dispatch. An inline button press that no
    handler claims is still acknowledged, so the user's client does not spin.

    Shutdown: drain() stops accepting new updates and lets the queue run out, waiting up to the
    given timeout for in-flight handlers to finish before cancelling them. Callers must stop the
    connection from receiving first, otherwise newly received updates are dropped rather than
    processed.
    """

    def __init__(
        self,
        registry: HandlerRegistry,
        template: HandlerContext,
        worker_tasks: int,
        queue_capacity: int,
    ) -> None:
        """Creates a dispatcher over the given registry and starts its workers, so it must be
        created on the running event loop.

        Args:
            registry: The handlers to dispatch to.
            template: The context template every per-update context is derived from.
            worker_tasks: The number of updates processed concurrently.
            queue_capacity: The maximum number of updates waiting to be processed.
        """
        self._registry = registry
        self._template = template
        self._queue: asyncio.Queue[Update] = asyncio.Queue(queue_capacity)
        self._accepting = True
        self._active = 0
        self._processed = 0
        self._dropped = 0
        self._workers = [
            asyncio.create_task(self._work(), name=f"spb-worker-{next(_worker_numbers)}")
            for _ in range(worker_tasks)
        ]

    def submit(self, update: Update) -> bool:
        """Enqueues an update for processing on a worker task.

        Returns:
            True when the update was queued, False when it was dropped because the queue is full
            or the dispatcher is shutting down.
        """
        if not self._accepting:
            self._dropped += 1
            log.debug("Update %s dropped: the dispatcher is shutting down", update.update_id)
            return False

        try:
            self._queue.put_nowait(update)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            log.warning(
                "Update %s dropped: the queue is full (%d waiting)",
                update.update_id,
                self.pending_updates,
            )
            return False

    @property
    def pending_updates(self) -> int:
        """The number of updates waiting for a worker."""
        return self._queue.qsize()

    @property
    def processed_updates(self) -> int:
        """How many updates have finished processing since start-up."""
        return self._processed

    @property
    def dropped_updates(self) -> int:
        """How many updates were discarded because the queue was full or the dispatcher was
        shutting down."""
        return self._dropped

    @property
    def is_accepting(self) -> bool:
        """Whether the dispatcher still accepts updates, which it does until shutdown begins."""
        return self._accepting

    async def drain(self, timeout: float) -> bool:
        """Stops accepting updates and processes everything already queued.

        Args:
            timeout: How many seconds to wait for the queue to run out and in-flight handlers to
                finish.

        Returns:
            True when everything drained in time, False when the remaining work was cancelled.
        """
        if not self._accepting:
            return all(worker.done() for worker in self._workers)
        self._accepting = False

        remaining = self.pending_updates
        if remaining > 0:
            log.info("Finishing %d queued update(s) before shutting down", remaining)

        drained = True
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except TimeoutError:
            log.warning(
                "%d update(s) did not finish within %ss, interrupting them",
                self.pending_updates + self._active,
                timeout,
            )
            drained = False

        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        return drained

    async def _work(self) -> None:
        while True:
            update = await self._queue.get()
            self._active += 1
            try:
                await self._process(update)
            finally:
                self._active -= 1
                self._queue.task_done()

    async def _process(self, update: Update) -> None:
        context = self._template.with_update(update)
        _track_media_group(context)

        try:
            for observer in self._registry.observers_for(context):
                await _run(observer, context, update)

            routed = self._registry.route_for(context)
            if routed is not None:
                await _run(routed, context, update)
                return

            await _unclaimed(context, update)
        except Exception:
            log.exception("Failed to process update %s (%s)", update.update_id, _describe(update))
        finally:
            self._processed += 1


def _track_media_group(context: HandlerContext) -> None:
    """Records this update's part of an album, if it is one.

    Telegram delivers an album as several messages sharing a media group id, each carrying one
    attachment, so no single update contains the whole thing. Anything that acts on an album,
    deleting all of it because one part was flagged, attaching all of it to a report, needs the
    parts seen so far.

    The dispatcher does this rather than a handler because the dispatcher is what sees every
    update: a handler that only runs for some of them would miss the rest of the album.
    """
    message = context.message
    if message is not None:
        media_group(context, message)


def media_group(context: HandlerContext, message: Message) -> MediaGroupState | None:
    """Adds a message to the state of the album it belongs to.

    Returns:
        The album's state, or None when the message is not part of an album, has no author, or
        another author's album is already tracked under its id.
    """
    if not message.media_group_id or message.from_user is None:
        return None

    author_id = message.from_user.id
    key = f"{MEDIA_GROUP_CACHE_PREFIX}{message.chat.id}:{message.media_group_id}"
    state = context.cache.get(key, lambda _: MediaGroupState(author_id))
    if not isinstance(state, MediaGroupState) or state.author_id != author_id:
        return None

    state.add_message_id(message.id)
    for attachment in report_attachments.collect(message):
        state.add_attachment(attachment)
    return state


async def _run(handler: Handler, context: HandlerContext, update: Update) -> None:
    """Invokes one handler, logging and swallowing whatever it raises."""
    try:
        await handler.handle(context)
    except Exception:
        log.warning(
            "Handler %s failed on update %s (%s)",
            handler.name,
            update.update_id,
            _describe(update),
            exc_info=True,
        )


async def _unclaimed(context: HandlerContext, update: Update) -> None:
    """Deals with an update no routed handler claimed.

    An unanswered inline button press leaves a loading indicator spinning in the user's client, so
    it is acknowledged even when nothing handled it.
    """
    if update.callback_query is None:
        log.debug("No handler claimed update %s (%s)", update.update_id, _describe(update))
        return

    try:
        await context.telegram_client.answer_callback_query(update.callback_query.id)
    except Exception:
        log.debug(
            "Failed to acknowledge unclaimed callback query on update %s",
            update.update_id,
            exc_info=True,
        )


def _describe(update: Update) -> str:
    """Returns a short description of an update for diagnostic logging."""
    type = UpdateType.of(update)
    description = type.name.lower() if type is not None else "unknown"

    if update.callback_query is not None:
        description += f" data={update.callback_query.data}"
    elif update.message is not None and update.message.text is not None:
        text = update.message.text
        description += f" text={text[:32] + '...' if len(text) > 32 else text}"

    return description
