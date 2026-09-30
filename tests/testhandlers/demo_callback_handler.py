from spb.classes.handler import Handler
from spb.classes.update_handler import update_handler
from spb.enums import UpdateType
from spb.objects.context.handler_context import HandlerContext
from tests.support import recorder


@update_handler(UpdateType.CALLBACK_QUERY, callback_data=("demo:",))
class DemoCallbackHandler(Handler):
    """Owns the demo: callback data namespace."""

    async def handle(self, context: HandlerContext) -> None:
        recorder.record("demo-callback", context)
