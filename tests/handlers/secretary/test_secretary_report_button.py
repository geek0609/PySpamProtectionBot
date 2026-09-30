from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from spb.classes.sessions.secretary_report_store import SecretaryReportStore
from spb.enums import SecretaryContactStatus
from spb.handlers.secretary import secretary_message_handler, secretary_settings_handler
from spb.handlers.secretary.secretary_message_handler import ContactDecision
from spb.objects.context.handler_context import HandlerContext
from spb.objects.secretary_report_target import SecretaryReportTarget


def test_allowing_or_denying_a_contact_keeps_the_report_button(template: HandlerContext) -> None:
    lm = template.languages
    lang = lm.default_language
    report = InlineKeyboardButton(
        "Report Message", callback_data=f"{secretary_settings_handler.REPORT_CALLBACK_PREFIX}:token"
    )
    allowed = secretary_message_handler.contact_decision_markup(
        lm, ContactDecision("conn", 42, lang, SecretaryContactStatus.ALLOWED)
    )
    notification = InlineKeyboardMarkup([*allowed.inline_keyboard, [report]])

    denied = secretary_message_handler.keep_report_button(
        secretary_message_handler.contact_decision_markup(
            lm, ContactDecision("conn", 42, lang, SecretaryContactStatus.DENIED)
        ),
        notification,
    )

    assert len(denied.inline_keyboard) == 2
    assert denied.inline_keyboard[1][0] is report


def test_a_report_token_only_resolves_for_the_owner_it_was_issued_to() -> None:
    store = SecretaryReportStore()
    token = store.store(SecretaryReportTarget(1, 2, 3, "spam", [], {}))

    assert store.find(token, 1) is not None
    assert store.find(token, 2) is None
