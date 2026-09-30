from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import recorder


@update_handler(UpdateType.ANY, priority=10_000, enabled=False)
class DisabledHandler(Handler):
    """Would outrank every other handler, but is switched off and must never run."""

    async def handle(self, context: HandlerContext) -> None:
        recorder.record("disabled", context)
