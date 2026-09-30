from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import recorder


@update_handler(UpdateType.COMMAND, commands=("ping", "PING"))
class PingHandler(Handler):
    """Claims /ping and /PING, exercising case-insensitive command matching."""

    async def handle(self, context: HandlerContext) -> None:
        recorder.record("ping", context)
