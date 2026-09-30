import pytest
from pyrogram.types import InlineKeyboardMarkup

from spb.handlers.help_handler import HelpHandler
from spb.objects.context.handler_context import HandlerContext
from tests.support import updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio


def callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_help_in_a_group_is_shown_only_to_the_caller(
    context: HandlerContext, client: RecordingClient
) -> None:
    await HelpHandler().handle(context.with_update(updates.group_message(1, "/help")))

    (sent,) = client.named("send_message")
    assert sent.kwargs["ephemeral_message_parameters"].receiver_user_id == updates.TESTER.id
    assert callbacks(sent.kwargs["reply_markup"]) == [
        "help:SECRETARY_MODE",
        "help:CHAT_PROTECTION",
        "help:SETTINGS",
        "help:FEDERATION_COMMANDS",
        "help:OPERATORS",
    ]


@pytest.mark.parametrize(
    ("data", "back"),
    [
        ("help:CHAT_PROTECTION", "help:MAIN"),
        ("help:SETTINGS", "help:MAIN"),
        ("help:SETTINGS:PRIVACY", "help:SETTINGS"),
        ("help:OPERATORS", "help:MAIN"),
        ("help:OPERATORS:BLACKLIST", "help:OPERATORS"),
    ],
)
async def test_every_page_leads_back_to_where_it_came_from(
    context: HandlerContext, client: RecordingClient, data: str, back: str
) -> None:
    await HelpHandler().handle(context.with_update(updates.callback_query(1, data)))

    (edit,) = client.named("edit_message_text")
    assert callbacks(edit.kwargs["reply_markup"])[-1] == back
    assert len(client.named("answer_callback_query")) == 1
