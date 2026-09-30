from pathlib import Path

import pytest

from spb import main
from spb.classes.command_line_options import usage


def test_help_prints_the_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["--help"]) == 0
    assert capsys.readouterr().out == usage() + "\n"


def test_an_unknown_option_is_an_invalid_invocation(capsys: pytest.CaptureFixture[str]) -> None:
    assert main.main(["--verbose"]) == 2
    assert "Unknown option '--verbose'" in capsys.readouterr().err


def test_a_missing_configuration_is_an_invalid_invocation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configuration = tmp_path / "missing.yml"

    assert main.main(["--config", str(configuration)]) == 2
    assert str(configuration) in capsys.readouterr().err
