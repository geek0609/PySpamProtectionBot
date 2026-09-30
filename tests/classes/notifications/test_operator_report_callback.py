import pytest
from pyfederation.enums import ClassificationFlag

from spb.classes.notifications import operator_report_callback

TELEGRAM_CALLBACK_DATA_LIMIT = 64
"""Telegram rejects inline buttons whose callback data exceeds this many bytes."""


@pytest.mark.parametrize(
    "action", [operator_report_callback.CLOSE, *(flag.name for flag in ClassificationFlag)]
)
def test_the_data_fits_telegrams_limit_for_every_action(action: str) -> None:
    data = operator_report_callback.data("0b6f3c2e-8d7c-4a53-9d7e-2f1f5c9a1b2c", action)

    assert len(data.encode()) <= TELEGRAM_CALLBACK_DATA_LIMIT, data
