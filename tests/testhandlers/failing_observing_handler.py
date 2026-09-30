from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, UpdateType
from spb.objects.context.handler_context import HandlerContext


@update_handler(UpdateType.ANY, mode=DispatchMode.OBSERVE, priority=1000)
class FailingObservingHandler(Handler):
    """An observer that always fails, proving one bad handler cannot stop the rest."""

    async def handle(self, context: HandlerContext) -> None:
        raise RuntimeError("this observer always fails")
