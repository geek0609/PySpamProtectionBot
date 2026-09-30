"""The private-chat /start screen.

It is shown by /start and again when the user returns to it from the language picker or from the
secretary settings menu. Keeping the rendering here means those cannot drift apart, and none of
their handlers has to reach into another's package to draw it.
"""

from pyrogram.types import InlineKeyboardMarkup

from spb.handlers import language_handler
from spb.handlers.secretary import secretary_settings_handler
from spb.objects.context.handler_context import HandlerContext
from spb.objects.language import Language
from spb.utilities import html_escape, message_helper


async def html(context: HandlerContext, lang: Language) -> str:
    """Describes the bot, how to get started, and the Federation server it works with."""
    lm = context.languages
    parts = [
        f"<b>{html_escape.escape(context.bot_name)}</b>\n\n",
        lm.get(lang, "start", "private_description"),
        lm.get(lang, "start", "getting_started_header"),
        lm.get(lang, "start", "getting_started_private_body"),
        lm.get(lang, "start", "additional_help_header"),
        lm.get(lang, "start", "additional_help_body"),
    ]

    server = await message_helper.server_information(context)
    if server is not None:
        host = html_escape.escape(message_helper.server_host(context))
        parts += [
            lm.get(lang, "start", "server_information_header"),
            lm.get(lang, "start", "server_name_label", html_escape.escape(server.name)),
            lm.get(lang, "start", "server_host_label", host),
            lm.get(
                lang, "start", "server_api_version_label", html_escape.escape(server.api_version)
            ),
        ]
    return "".join(parts)


def markup(context: HandlerContext, lang: Language, user_id: int | None) -> InlineKeyboardMarkup:
    """Offers adding the bot to a group, choosing a language, and, for a user with secretary mode
    enabled, its settings."""
    lm = context.languages
    primary = []
    if context.bot_username.strip():
        primary.append(
            message_helper.url_button(
                lm.get(lang, "buttons", "add_to_group"),
                f"https://t.me/{context.bot_username}?startgroup=add",
            )
        )
    primary.append(
        message_helper.button(
            lm.get(lang, "buttons", "set_language"),
            f"{language_handler.CALLBACK_PREFIX}:start_menu",
        )
    )

    rows = [primary]
    secretaries = context.managers.secretary_configurations
    if user_id is not None and secretaries.secretary_configuration_exists(user_id):
        rows.append(
            [
                message_helper.button(
                    lm.get(lang, "buttons", "secretary_settings"),
                    secretary_settings_handler.OPEN_WITH_BACK_CALLBACK,
                )
            ]
        )
    return message_helper.markup(*rows)
