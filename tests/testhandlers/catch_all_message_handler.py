from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import recorder


@update_handler(UpdateType.MESSAGE, priority=-100)
class CatchAllMessageHandler(Handler):
    """Last in line for any message no other handler claimed."""

    async def handle(self, context: HandlerContext) -> None:
        recorder.record("catch-all", context)
