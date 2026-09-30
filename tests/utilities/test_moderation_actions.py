import time

from spb.utilities import moderation_actions


def test_until_from_now_is_an_absolute_time_in_the_future() -> None:
    before = int(time.time())
    until = moderation_actions.until_from_now(3_600)
    after = int(time.time())

    # Telegram's until date is absolute: a bare 3600 would mean 1970, which Telegram treats as
    # "forever" and turns a one-hour mute into a permanent restriction.
    assert before + 3_600 <= until <= after + 3_600


def test_a_temporary_restriction_defaults_to_a_day_from_now() -> None:
    now = int(time.time())
    until = moderation_actions.temporary_restriction_until(None)
    assert now + 86_400 <= until <= now + 86_401

    # A suggested lift time too close to now is not honoured by Telegram, so the default applies.
    assert moderation_actions.temporary_restriction_until(now + 5) >= now + 86_400

    suggested = now + 7_200
    assert moderation_actions.temporary_restriction_until(suggested) == suggested
