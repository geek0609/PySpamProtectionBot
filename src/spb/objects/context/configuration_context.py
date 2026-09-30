"""An open configuration menu."""

from dataclasses import dataclass, replace
from typing import Self

from spb.enums import ConfigurationPage


@dataclass(frozen=True, slots=True)
class ConfigurationContext:
    """An open configuration menu for one chat, owned by the user who opened it.

    Attributes:
        hash: The opaque id carried in the menu's callback data.
        user_id: The user who opened the menu.
        chat_id: The chat being configured.
        page: The page shown.
        channel_link_verification_code: The code shown on the Chat Linking page, or None.
        message_id: The message the menu is shown in, or None.
        ephemeral: Whether the menu is an ephemeral message only its owner sees.
        ephemeral_message_id: The ephemeral message the menu is shown in, or None.
        show_back_button: Whether the menu offers a way back to where it was opened from.
    """

    hash: str
    user_id: int
    chat_id: int
    page: ConfigurationPage = ConfigurationPage.MAIN
    channel_link_verification_code: int | None = None
    message_id: int | None = None
    ephemeral: bool = False
    ephemeral_message_id: int | None = None
    show_back_button: bool = False

    def touch(self) -> Self:
        """Returns the session as it is; storing it again is what refreshes its expiry."""
        return self

    def with_page(self, page: ConfigurationPage) -> Self:
        return replace(self, page=page)

    def with_channel_link_verification_code(self, code: int | None) -> Self:
        return replace(self, channel_link_verification_code=code)

    def with_message_id(self, message_id: int | None) -> Self:
        return replace(self, message_id=message_id)

    def with_ephemeral_message_id(self, ephemeral_message_id: int | None) -> Self:
        return replace(self, ephemeral_message_id=ephemeral_message_id)

    def with_show_back_button(self, show_back_button: bool) -> Self:
        return replace(self, show_back_button=show_back_button)
