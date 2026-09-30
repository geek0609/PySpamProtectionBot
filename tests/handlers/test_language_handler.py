import pytest
from pyrogram.types import InlineKeyboardMarkup

from spb.handlers.language_handler import LanguageHandler
from spb.objects.context.handler_context import HandlerContext
from tests.support import updates
from tests.support.recording_client import RecordingClient

pytestmark = pytest.mark.anyio


def callbacks(markup: InlineKeyboardMarkup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_the_private_picker_offers_every_language(
    context: HandlerContext, client: RecordingClient
) -> None:
    await LanguageHandler().handle(context.with_update(updates.private_message(1, "/language")))

    (sent,) = client.named("send_message")
    codes = [language.code for language in context.languages.available_languages]
    assert callbacks(sent.kwargs["reply_markup"]) == [f"lang:{code}" for code in codes]


async def test_choosing_a_language_saves_it_and_answers_in_it(
    context: HandlerContext, client: RecordingClient
) -> None:
    await LanguageHandler().handle(context.with_update(updates.callback_query(1, "lang:es")))

    spanish = context.managers.language_preferences.get_user_language(42)
    assert spanish.code == "es"
    (edit,) = client.named("edit_message_text")
    assert "lang:start" not in callbacks(edit.kwargs["reply_markup"])
    (answer,) = client.named("answer_callback_query")
    assert answer.kwargs["text"] == context.languages.get(
        spanish, "general", "language.changed", spanish.emoji, spanish.name
    )


async def test_a_picker_opened_from_start_leads_back_to_it(
    context: HandlerContext, client: RecordingClient
) -> None:
    await LanguageHandler().handle(context.with_update(updates.callback_query(1, "lang:es:start")))

    (edit,) = client.named("edit_message_text")
    rows = callbacks(edit.kwargs["reply_markup"])
    assert rows[-1] == "lang:start"
    assert all(data.endswith(":start") for data in rows)


async def test_back_restores_the_start_screen(
    context: HandlerContext, client: RecordingClient
) -> None:
    await LanguageHandler().handle(context.with_update(updates.callback_query(1, "lang:start")))

    (edit,) = client.named("edit_message_text")
    assert context.bot_name in edit.kwargs["text"]
    assert "lang:start_menu" in callbacks(edit.kwargs["reply_markup"])


async def test_a_language_no_longer_shipped_is_ignored(
    context: HandlerContext, client: RecordingClient
) -> None:
    await LanguageHandler().handle(context.with_update(updates.callback_query(1, "lang:xx")))

    assert client.named("edit_message_text") == []
    assert len(client.named("answer_callback_query")) == 1


async def test_only_group_administrators_change_the_group_language(
    context: HandlerContext, client: RecordingClient
) -> None:
    client.respond("get_chat_members", [])

    await LanguageHandler().handle(context.with_update(updates.group_message(1, "/language")))

    (sent,) = client.named("send_message")
    assert sent.kwargs["ephemeral_message_parameters"].receiver_user_id == updates.TESTER.id
    assert sent.kwargs.get("reply_markup") is None
