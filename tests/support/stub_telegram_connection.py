from pyrogram import Client

from spb.objects.bot_identity import BotIdentity


class StubTelegramConnection:
    """A Telegram connection that never opens one.

    Most handler logic, deciding what an update is, whether to claim it and what to say, happens
    before anything is sent. Tests of that logic need the bot's identity and nothing else, so this
    supplies an identity and, unless a test brings its own stand-in, no client: a test that
    unexpectedly tries to send something fails loudly rather than quietly talking to a mock.
    """

    def __init__(self, identity: BotIdentity, client: Client | None = None) -> None:
        self._identity = identity
        self._client = client

    @property
    def client(self) -> Client:
        if self._client is None:
            raise AssertionError("the test tried to reach Telegram")
        return self._client

    @property
    def identity(self) -> BotIdentity:
        return self._identity
