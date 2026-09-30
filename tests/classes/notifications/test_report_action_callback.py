"""Tests for the callback data carried by moderation buttons, which must stay within Telegram's
limit and carry everything the action needs, since no server-side state backs them."""

import pytest

from spb.classes.notifications.report_action_callback import Action, ReportActionCallback
from spb.objects.admin_info import AdminInfo

TELEGRAM_CALLBACK_DATA_LIMIT = 64
"""Telegram rejects inline buttons whose callback data exceeds this many bytes."""


@pytest.mark.parametrize("action", list(Action))
def test_the_data_fits_telegrams_limit_even_with_the_largest_ids(action: Action) -> None:
    data = ReportActionCallback(-(2**63), 2**31 - 1, -(2**63), action).data

    assert len(data.encode()) <= TELEGRAM_CALLBACK_DATA_LIMIT, data


def test_the_data_parses_back_to_the_same_message_and_action() -> None:
    button = ReportActionCallback(-1001234567890, 4567, 7_000_000_001, Action.MUTE)

    assert ReportActionCallback.parse(button.data) == button


def test_malformed_data_is_not_mistaken_for_a_button() -> None:
    assert ReportActionCallback.parse("report:0b6f3c2e-8d7c-4a53-9d7e-2f1f5c9a1b2c:ban") is None
    assert ReportActionCallback.parse("rpa:-100123:45:67:explode") is None
    assert ReportActionCallback.parse("rpa:-100123:notanumber:67:ban") is None


def test_each_action_asks_for_the_right_it_needs() -> None:
    deleter = AdminInfo(1, False, True, False, False)

    assert Action.DELETE.permits(deleter)
    assert not Action.BAN.permits(deleter)
    assert not Action.MUTE.permits(None)
