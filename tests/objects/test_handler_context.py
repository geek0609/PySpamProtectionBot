import pytest

from spb.objects.context.handler_context import HandlerContext
from tests.support import contexts, updates


def test_the_derived_context_carries_the_update_and_shares_every_service(
    template: HandlerContext,
) -> None:
    update = updates.private_message(1, "hello")

    derived = template.with_update(update)

    assert derived is not template
    assert derived.update is update
    assert derived.cache is template.cache
    assert derived.bot is template.bot
    assert derived.current_update is update
    assert template.update is None
    with pytest.raises(RuntimeError, match="template"):
        _ = template.current_update


def test_a_message_is_found_wherever_it_arrived(template: HandlerContext) -> None:
    assert _text(template.with_update(updates.private_message(1, "hello"))) == "hello"
    assert _text(template.with_update(updates.channel_post(2, "post"))) == "post"
    assert _text(template.with_update(updates.business_message(3, "business"))) == "business"


def test_a_button_press_exposes_the_message_it_was_attached_to(template: HandlerContext) -> None:
    assert _text(template.with_update(updates.callback_query(1, "demo:open"))) == "menu"


def test_an_update_carrying_no_message_says_so(template: HandlerContext) -> None:
    assert template.with_update(updates.business_connection(1, True)).message is None
    assert template.message is None


def test_the_chat_and_author_are_read_off_the_message(template: HandlerContext) -> None:
    context = template.with_update(updates.group_message(1, "hello"))

    assert context.chat_id == -1001234567890
    assert context.user_id == 42
    assert context.user is not None


def test_a_channel_post_has_no_author(template: HandlerContext) -> None:
    context = template.with_update(updates.channel_post(1, "post"))

    assert context.user_id is None
    assert context.chat_id == -1009876543210


def test_the_bot_identity_is_exposed(template: HandlerContext) -> None:
    assert template.bot == contexts.BOT
    assert template.bot_user_id == 1000
    assert template.bot_username == "testbot"
    assert template.bot_name == "Test Bot"


def test_the_command_name_is_lowercased_and_stripped_of_its_decorations(
    template: HandlerContext,
) -> None:
    assert _command(template, "/start") == "start"
    assert _command(template, "/start@testbot") == "start"
    assert _command(template, "/START payload") == "start"
    assert _command(template, "  /start  ") == "start"


def test_text_that_is_not_a_command_has_no_command_name(template: HandlerContext) -> None:
    assert _command(template, "hello") is None
    assert _command(template, "/") is None
    assert _command(template, None) is None
    assert template.with_update(updates.callback_query(4, "demo:open")).command_name is None


def test_the_payload_is_whatever_follows_the_command_token(template: HandlerContext) -> None:
    assert _payload(template, "/start payload") == "payload"
    assert _payload(template, "/start two words") == "two words"
    assert _payload(template, "/start@testbot payload") == "payload"


def test_a_command_without_a_payload_has_none(template: HandlerContext) -> None:
    assert _payload(template, "/start") is None
    assert _payload(template, "/start   ") is None


def test_the_callback_query_and_its_data_are_exposed(template: HandlerContext) -> None:
    context = template.with_update(updates.callback_query(1, "demo:open"))

    assert context.callback_query is not None
    assert context.callback_data == "demo:open"
    assert context.user_id == 42


def test_an_update_that_is_not_a_button_press_has_no_callback_data(
    template: HandlerContext,
) -> None:
    context = template.with_update(updates.private_message(1, "hello"))

    assert context.callback_query is None
    assert context.callback_data is None


def _text(context: HandlerContext) -> str | None:
    message = context.message
    assert message is not None
    return message.text


def _command(template: HandlerContext, text: str | None) -> str | None:
    return template.with_update(updates.private_message(1, text)).command_name


def _payload(template: HandlerContext, text: str | None) -> str | None:
    return template.with_update(updates.private_message(1, text)).command_payload
