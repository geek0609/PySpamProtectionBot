"""Links into a Federation Web Application instance."""

from enum import Enum
from typing import Self
from urllib.parse import quote

from pyfederation.records import EntityQueryResult
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from spb.classes.configuration import Configuration
from spb.classes.language_manager import LanguageManager
from spb.objects.language import Language


class Record(Enum):
    """A Federation record type the web application has a detail page for."""

    REPORT = "reports"
    EVIDENCE = "evidence"
    ENTITY = "entities"
    BLACKLIST = "blacklist"
    OPERATOR = "operators"


class FederationWebLinks:
    """Builds links into a Federation Web Application instance, so a message displaying a
    Federation record can offer a button opening that same record in the browser.

    The web application is optional: when federation.web_application_endpoint is not configured,
    every method here yields nothing and callers send their messages unchanged. Callers therefore
    never branch on whether links are enabled themselves; they pass whatever button() returns to
    attach(), which skips a None button.
    """

    def __init__(self, endpoint: str | None) -> None:
        """
        Args:
            endpoint: The web application base URL, or None to disable links.
        """
        if endpoint is None or not endpoint.strip():
            self._base_url = None
        else:
            self._base_url = endpoint if endpoint.endswith("/") else f"{endpoint}/"

    @classmethod
    def from_configuration(cls, configuration: Configuration) -> Self:
        """Creates the links configured for this process."""
        return cls(configuration.federation_web_application_endpoint)

    @property
    def is_enabled(self) -> bool:
        """Whether a web application is configured."""
        return self._base_url is not None

    def url(self, record: Record, id: str | None) -> str | None:
        """Returns the URL of a record's detail page in the web application.

        Args:
            record: The record type.
            id: The record UUID (or, for an entity, any identifier the web application resolves).

        Returns:
            The detail page URL, or None when links are disabled or the id is blank.
        """
        if self._base_url is None or id is None or not id.strip():
            return None

        return f"{self._base_url}{record.value}/{quote(id, safe='')}"

    def button(
        self, languages: LanguageManager, language: Language | None, record: Record, id: str | None
    ) -> InlineKeyboardButton | None:
        """Returns a button opening a record's detail page in the web application.

        Returns:
            The button, or None when links are disabled or the id is blank.
        """
        url = self.url(record, id)
        if url is None:
            return None

        match record:
            case Record.REPORT:
                label = languages.get(language, "buttons", "view_report")
            case Record.EVIDENCE:
                label = languages.get(language, "buttons", "view_evidence")
            case Record.ENTITY:
                label = languages.get(language, "buttons", "view_entity")
            case Record.BLACKLIST:
                label = languages.get(language, "buttons", "view_blacklist")
            case Record.OPERATOR:
                label = languages.get(language, "buttons", "view_operator")
        return InlineKeyboardButton(label, url=url)

    def entity_button(
        self, languages: LanguageManager, language: Language | None, query: EntityQueryResult | None
    ) -> InlineKeyboardButton | None:
        """Returns a button opening the entity an entity query resolved to.

        Args:
            query: The entity query, or None when the entity was not queried.

        Returns:
            The button, or None when links are disabled or the query resolved no entity.
        """
        if query is None:
            return None
        return self.button(languages, language, Record.ENTITY, query.entity_record.uuid)

    @staticmethod
    def attach(
        markup: InlineKeyboardMarkup | None, *buttons: InlineKeyboardButton | None
    ) -> InlineKeyboardMarkup | None:
        """Returns the keyboard with the given link buttons appended as one extra row.

        None buttons are skipped, so the result of button() can be passed straight in.

        Returns:
            The extended keyboard, or the original markup (possibly None) when there is no button
            to append.
        """
        present = [button for button in buttons if button is not None]
        if not present:
            return markup

        rows = list(markup.inline_keyboard) if markup is not None else []
        return InlineKeyboardMarkup([*rows, present])

    @staticmethod
    def keep_links(
        replacement: InlineKeyboardMarkup | None, previous: InlineKeyboardMarkup | None
    ) -> InlineKeyboardMarkup | None:
        """Returns a replacement keyboard followed by the link buttons of the keyboard it replaces,
        so editing a message's actions does not drop its link to the web application."""
        links = FederationWebLinks.links_only(previous).inline_keyboard
        if not links:
            return replacement

        rows = list(replacement.inline_keyboard) if replacement is not None else []
        return InlineKeyboardMarkup([*rows, *links])

    @staticmethod
    def links_only(markup: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup:
        """Returns the keyboard left once every button that is not a link is removed, so a message
        whose actions have been used up keeps its link to the web application."""
        rows = []
        for row in markup.inline_keyboard if markup is not None else []:
            links = [button for button in row if button.url is not None]
            if links:
                rows.append(links)
        return InlineKeyboardMarkup(rows)


DISABLED = FederationWebLinks(None)
"""Links that are never produced, used when no web application is configured."""
