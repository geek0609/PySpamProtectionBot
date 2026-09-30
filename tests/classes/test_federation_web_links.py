from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from spb.classes.federation_web_links import DISABLED, FederationWebLinks, Record
from spb.classes.language_manager import LanguageManager

UUID = "0199a3c4-5b6d-7e8f-9a0b-1c2d3e4f5a6b"


def test_normalizes_trailing_slash() -> None:
    expected = f"https://federation.example.com/reports/{UUID}"

    assert (
        FederationWebLinks("https://federation.example.com/").url(Record.REPORT, UUID) == expected
    )
    assert FederationWebLinks("https://federation.example.com").url(Record.REPORT, UUID) == expected
    assert (
        FederationWebLinks("https://example.com/federation").url(Record.ENTITY, UUID)
        == f"https://example.com/federation/entities/{UUID}"
    )


def test_disabled_produces_nothing() -> None:
    assert DISABLED.url(Record.EVIDENCE, UUID) is None
    assert DISABLED.button(LanguageManager("en"), None, Record.EVIDENCE, UUID) is None


def test_buttons_carry_the_translated_label() -> None:
    links = FederationWebLinks("https://federation.example.com/")

    button = links.button(LanguageManager("en"), None, Record.REPORT, UUID)

    assert button is not None
    assert button.text == "View Report"
    assert button.url == f"https://federation.example.com/reports/{UUID}"


def test_links_only_keeps_link_buttons() -> None:
    action = InlineKeyboardButton("Close", callback_data="close")
    link = InlineKeyboardButton("View Report", url=f"https://federation.example.com/reports/{UUID}")
    markup = InlineKeyboardMarkup([[action], [link]])

    assert FederationWebLinks.links_only(markup).inline_keyboard == [[link]]


def test_keep_links_appends_the_previous_link_rows() -> None:
    link = InlineKeyboardButton("View Report", url=f"https://federation.example.com/reports/{UUID}")
    replacement = InlineKeyboardMarkup([[InlineKeyboardButton("Next", callback_data="next")]])

    kept = FederationWebLinks.keep_links(replacement, InlineKeyboardMarkup([[link]]))

    assert kept is not None
    assert kept.inline_keyboard[-1] == [link]
    assert FederationWebLinks.keep_links(replacement, None) is replacement
