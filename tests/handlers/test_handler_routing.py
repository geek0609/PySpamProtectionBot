"""Tests that the shipped handlers claim the updates meant for them, and only those."""

import pytest
from pyrogram.types import MessageOriginUser, User

from spb.classes.handler_registry import HandlerRegistry
from spb.objects.context.handler_context import HandlerContext
from spb.objects.update import Update
from tests.support import updates


@pytest.fixture(scope="module")
def registry() -> HandlerRegistry:
    return HandlerRegistry.discover()


def route(registry: HandlerRegistry, template: HandlerContext, update: Update) -> str:
    """Returns the class name of the handler that claims the update, or "none"."""
    handler = registry.route_for(template.with_update(update))
    return handler.name if handler is not None else "none"


def observers(registry: HandlerRegistry, template: HandlerContext, update: Update) -> list[str]:
    return [handler.name for handler in registry.observers_for(template.with_update(update))]


def forwarded(update: Update) -> Update:
    assert update.message is not None
    someone = User(id=7, is_bot=False, first_name="Someone")
    update.message.forward_origin = MessageOriginUser(
        type="user", date=updates.DATE, sender_user=someone
    )
    return update


# Routing commands


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/start", "StartHandler"),
        ("/help", "HelpHandler"),
        ("/settings", "SettingsHandler"),
        ("/language", "LanguageHandler"),
        ("/lang", "LanguageHandler"),
        ("/info 12345", "InfoHandler"),
        ("/evidence abc", "EvidenceHandler"),
        ("/link a b proxy", "LinkHandler"),
        ("/auth token", "AuthenticationHandler"),
        ("/authinfo", "AuthenticationHandler"),
        ("/deauth", "AuthenticationHandler"),
        ("/connect 1", "ChannelConnectHandler"),
        ("/blacklist a b", "BlacklistHandler"),
        ("/report", "ReportHandler"),
        ("/ping", "PingHandler"),
    ],
)
def test_each_command_reaches_its_own_handler(
    registry: HandlerRegistry, template: HandlerContext, text: str, expected: str
) -> None:
    assert route(registry, template, updates.group_message(1, text)) == expected


def test_the_botname_suffix_still_routes(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route(registry, template, updates.group_message(1, "/help@testbot")) == "HelpHandler"
    assert route(registry, template, updates.group_message(2, "/info@testbot 42")) == "InfoHandler"


def test_an_unknown_command_is_left_unclaimed(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route(registry, template, updates.group_message(1, "/nosuchcommand")) == "none"


def test_an_ordinary_group_message_is_not_claimed_by_a_command_handler(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route(registry, template, updates.group_message(1, "just chatting")) == "none"


def test_a_forwarded_start_is_a_message_to_report_not_an_invocation(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    # Forwarding a message into the bot's private chat is how a user reports it, so a forwarded
    # /start is somebody reporting the text "/start" rather than running it.
    update = forwarded(updates.private_message(1, "/start"))

    assert route(registry, template, update) == "ReportHandler"


def test_a_forwarded_message_in_a_group_is_not_a_report(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = forwarded(updates.group_message(1, "look at this"))

    assert route(registry, template, update) == "none"


def test_settings_in_a_private_chat_is_claimed_by_secretary_settings(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.private_message(1, "/settings")

    assert route(registry, template, update) == "SecretarySettingsHandler"


def test_settings_in_a_group_belongs_to_the_group_settings_handler(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route(registry, template, updates.group_message(1, "/settings")) == "SettingsHandler"


def test_a_plain_start_opens_the_normal_start_handler_not_secretary_mode(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route(registry, template, updates.private_message(1, "/start")) == "StartHandler"


def test_a_start_carrying_the_business_deep_link_opens_secretary_settings(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.private_message(1, "/start bizChat123")

    assert route(registry, template, update) == "SecretarySettingsHandler"


# Routing inline buttons


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("cfg:main", "ConfigurationHandler"),
        ("help:main", "HelpHandler"),
        ("lang:menu", "LanguageHandler"),
        ("report:open", "ReportHandler"),
        ("rpa:-100123:45:67:ban", "ReportActionHandler"),
        ("secset:open", "SecretarySettingsHandler"),
        ("seccontact:allow", "SecretarySettingsHandler"),
        ("secreport:token", "SecretarySettingsHandler"),
        ("opr:close", "OperatorReportHandler"),
    ],
)
def test_each_callback_prefix_reaches_its_owner(
    registry: HandlerRegistry, template: HandlerContext, data: str, expected: str
) -> None:
    assert route(registry, template, updates.callback_query(1, data)) == expected


def test_a_callback_in_no_handlers_namespace_is_left_unclaimed(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    assert route(registry, template, updates.callback_query(1, "unknown:thing")) == "none"


def test_no_two_handlers_claim_the_same_callback_prefix(registry: HandlerRegistry) -> None:
    prefixes = [prefix for handler in registry.handlers for prefix in handler.callback_prefixes]

    for prefix in prefixes:
        claimants = [
            handler.name
            for handler in registry.handlers
            if any(prefix.startswith(claimed) for claimed in handler.callback_prefixes)
        ]
        assert len(claimants) == 1, f"callback prefix '{prefix}' is claimed by {claimants}"


# Routing Telegram Business updates


def test_the_connection_itself_is_handled_by_the_connection_handler(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    for update in (updates.business_connection(1, True), updates.business_connection(2, False)):
        assert route(registry, template, update) == "SecretaryConnectionHandler"


def test_a_message_arriving_through_the_connection_is_handled_by_the_message_handler(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.business_message(1, "hello there")

    assert route(registry, template, update) == "SecretaryMessageHandler"


def test_a_business_message_deletion_is_handled_by_the_message_handler_too(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.deleted_business_messages(1)

    assert route(registry, template, update) == "SecretaryMessageHandler"


def test_a_business_message_is_not_claimed_by_the_group_message_handlers(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    # Group bookkeeping and scanning are for chat messages, not business ones.
    assert observers(registry, template, updates.business_message(1, "hello")) == []


# Observing every update


def test_scanning_sees_updates_no_routed_handler_claims(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.group_message(1, "just chatting")

    assert "ScanningHandler" in observers(registry, template, update)
    assert route(registry, template, update) == "none"


def test_scanning_also_sees_commands_before_the_command_handler_runs(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.group_message(1, "/report")

    assert "ScanningHandler" in observers(registry, template, update)
    assert route(registry, template, update) == "ReportHandler"


def test_registration_sees_a_message_before_anything_answers_it(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.private_message(1, "/start")

    assert observers(registry, template, update)[0] == "RegistrationHandler"


def test_join_protection_only_observes_membership_shaped_updates(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.callback_query(1, "cfg:main")

    assert "JoinProtectionHandler" not in observers(registry, template, update)


def test_a_member_joining_is_observed_by_join_protection(
    registry: HandlerRegistry, template: HandlerContext
) -> None:
    update = updates.chat_member_joined(1)

    assert "JoinProtectionHandler" in observers(registry, template, update)
