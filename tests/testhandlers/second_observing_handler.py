from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import DispatchMode, UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import recorder


@update_handler(UpdateType.ANY, mode=DispatchMode.OBSERVE, priority=5)
class SecondObservingHandler(Handler):
    """A second observer, ordered after ObservingHandler by its lower priority."""

    async def handle(self, context: HandlerContext) -> None:
        recorder.record("observer-second", context)
