from pyrogram.types import User

from spb.classes.notifications import notification_formatter
from spb.objects.context.handler_context import HandlerContext
from spb.objects.database.user_identity import UserIdentity


def test_a_user_is_mentioned_by_username_or_linked_by_name(template: HandlerContext) -> None:
    lm, lang = template.languages, template.languages.default_language

    tester = User(id=42, first_name="Tester", username="tester")
    assert notification_formatter.user_mention(lm, lang, tester) == "@tester"

    named = User(id=43, first_name="<b>Ann</b>", last_name="Lee")
    assert notification_formatter.user_mention(lm, lang, named) == (
        '<a href="tg://user?id=43">&lt;b&gt;Ann&lt;/b&gt; Lee</a> (<code>43</code>)'
    )


def test_a_user_id_is_mentioned_by_its_stored_identity(template: HandlerContext) -> None:
    lm, lang = template.languages, template.languages.default_language
    users = template.managers.users
    users.save_user(UserIdentity(42, "tester", "Tester", None))

    assert notification_formatter.user_id_mention(lm, lang, 42, users) == "@tester"
    assert '<a href="tg://user?id=7">' in notification_formatter.user_id_mention(lm, lang, 7, users)


def test_untrusted_detail_is_escaped_and_bounded(template: HandlerContext) -> None:
    lm, lang = template.languages, template.languages.default_language

    truncated = notification_formatter.escape_and_truncate(lm, lang, "<abcdef>", 4)

    assert truncated == "&lt;abc" + chr(0x2026)
    assert notification_formatter.escape_and_truncate(lm, lang, " ", 4) == lm.get(
        lang, "chat_notification", "no_text"
    )
    assert notification_formatter.chat_reference(lm, lang, "Group", -100) == (
        "Group (<code>-100</code>)"
    )
