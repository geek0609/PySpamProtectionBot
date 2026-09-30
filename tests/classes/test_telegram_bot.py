from pathlib import Path

import pytest
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import Chat, ChatMember, ChatMemberUpdated, User

from spb.classes.telegram_bot import TelegramBot
from spb.objects.bot_identity import BotIdentity
from spb.objects.update import Update
from tests.support import configurations, updates

BOT = User(id=1000, is_bot=True, first_name="Test Bot", username="testbot")


@pytest.fixture
def bot(tmp_path: Path) -> TelegramBot:
    bot = TelegramBot(configurations.minimal(tmp_path), tmp_path / "database.session")
    bot._identity = BotIdentity(BOT.id, BOT.username, BOT.first_name)
    return bot


def membership_change(user: User) -> ChatMemberUpdated:
    return ChatMemberUpdated(
        chat=Chat(id=-100, type=ChatType.SUPERGROUP),
        from_user=updates.TESTER,
        date=updates.DATE,
        old_chat_member=ChatMember(status=ChatMemberStatus.LEFT, user=user),
        new_chat_member=ChatMember(status=ChatMemberStatus.MEMBER, user=user),
    )


@pytest.mark.anyio
async def test_holds_updates_until_a_consumer_is_attached(bot: TelegramBot) -> None:
    received: list[Update] = []
    first = updates.private_message(1, "hello").message
    second = updates.private_message(2, "again").message

    await bot._on_message(bot.client, first)
    bot.start_receiving(received.append)
    await bot._on_message(bot.client, second)

    assert [update.message for update in received] == [first, second]
    assert [update.update_id for update in received] == [1, 2]


@pytest.mark.anyio
async def test_drops_updates_once_stopped(bot: TelegramBot) -> None:
    received: list[Update] = []
    bot.start_receiving(received.append)
    bot.stop_receiving()

    await bot._on_message(bot.client, updates.private_message(1, "hello").message)

    assert received == []


@pytest.mark.anyio
async def test_sorts_channel_posts_from_messages(bot: TelegramBot) -> None:
    received: list[Update] = []
    bot.start_receiving(received.append)

    await bot._on_message(bot.client, updates.channel_post(1, "post").channel_post)
    await bot._on_message(bot.client, updates.group_message(2, "hello").message)

    assert received[0].channel_post is not None
    assert received[0].message is None
    assert received[1].message is not None
    assert received[1].channel_post is None


@pytest.mark.anyio
async def test_sorts_the_bots_own_membership_from_other_members(bot: TelegramBot) -> None:
    received: list[Update] = []
    bot.start_receiving(received.append)

    await bot._on_chat_member_updated(bot.client, membership_change(BOT))
    await bot._on_chat_member_updated(bot.client, membership_change(updates.TESTER))

    assert received[0].my_chat_member is not None
    assert received[0].chat_member is None
    assert received[1].chat_member is not None
    assert received[1].my_chat_member is None


def test_identity_is_unknown_before_authentication(tmp_path: Path) -> None:
    bot = TelegramBot(configurations.minimal(tmp_path), tmp_path / "database.session")

    with pytest.raises(RuntimeError):
        _ = bot.identity
