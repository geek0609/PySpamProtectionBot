from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import recorder


@update_handler(UpdateType.COMMAND, commands=("ping",), priority=100)
class VetoingPingHandler(Handler):
    """Also claims /ping, ahead of PingHandler, but only takes the ones carrying the payload
    "mine"; the rest fall through to the next handler."""

    def accepts(self, context: HandlerContext) -> bool:
        return context.command_payload == "mine"

    async def handle(self, context: HandlerContext) -> None:
        recorder.record("veto-ping", context)
