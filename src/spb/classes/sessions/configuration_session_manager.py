"""The open configuration menus."""

from datetime import timedelta

from spb.classes.sessions.abstract_session_manager import AbstractSessionManager, ReverseIndex
from spb.enums import ConfigurationPage
from spb.objects.context.configuration_context import ConfigurationContext

SESSION_EXPIRY = timedelta(minutes=5)


class ConfigurationSessionManager(AbstractSessionManager[ConfigurationContext]):
    """The open configuration menus, and the Chat Linking codes they show.

    A notification chat links itself by sending the code shown on a menu's Chat Linking page, so
    the menu is also found by that code. Leaving the page discards the code.
    """

    def __init__(self) -> None:
        super().__init__(SESSION_EXPIRY)
        self._verification_index: ReverseIndex[int] = ReverseIndex(SESSION_EXPIRY)

    def create(self, user_id: int, chat_id: int) -> ConfigurationContext:
        """Opens a menu shown as a regular message."""
        return self._store(ConfigurationContext(self._generate_hash(), user_id, chat_id))

    def create_ephemeral(self, user_id: int, chat_id: int) -> ConfigurationContext:
        """Opens a menu shown as an ephemeral message only its owner sees."""
        return self._store(
            ConfigurationContext(self._generate_hash(), user_id, chat_id, ephemeral=True)
        )

    def update_page(self, hash: str, page: ConfigurationPage) -> ConfigurationContext | None:
        """Moves a menu to another page, discarding its code when it leaves Chat Linking.

        Returns:
            The updated session, or None when it has expired.
        """
        session = self.find(hash)
        if session is None:
            return None

        if session.page is ConfigurationPage.CHANNEL and page is not ConfigurationPage.CHANNEL:
            self._verification_index.clear(session.channel_link_verification_code)
            session = session.with_channel_link_verification_code(None)

        return self._store(session.with_page(page))

    def update_message_id(self, hash: str, message_id: int | None) -> None:
        session = self.find(hash)
        if session is not None:
            self._store(session.with_message_id(message_id))

    def update_show_back_button(self, hash: str, show_back_button: bool) -> None:
        session = self.find(hash)
        if session is not None:
            self._store(session.with_show_back_button(show_back_button))

    def update_ephemeral_message_id(self, hash: str, ephemeral_message_id: int | None) -> None:
        session = self.find(hash)
        if session is not None:
            self._store(session.with_ephemeral_message_id(ephemeral_message_id))

    def update_channel_link_verification_code(self, hash: str, code: int | None) -> None:
        """Records the code a menu shows, so the chat sending it can be linked to the menu's
        chat."""
        session = self.find(hash)
        if session is None:
            return

        self._verification_index.clear(session.channel_link_verification_code)
        self._store(session.with_channel_link_verification_code(code))
        if code is not None:
            self._verification_index.store(code, hash)

    def find_by_channel_link_verification_code(self, code: int) -> ConfigurationContext | None:
        """Returns the menu showing a code, or None when no open menu shows it."""
        hash = self._verification_index.resolve(code)
        if hash is None:
            return None

        session = self.find(hash)
        if session is None or session.channel_link_verification_code != code:
            # The index outlived the session, or the session has since shown a different code.
            self._verification_index.clear(code)
            return None
        return session

    def invalidate(self, hash: str) -> None:
        session = self._cache.get_if_present(hash)
        if session is not None:
            self._verification_index.clear(session.channel_link_verification_code)
        super().invalidate(hash)
