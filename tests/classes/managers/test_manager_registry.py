from pathlib import Path

from spb.classes.database import Database
from spb.classes.language_manager import LanguageManager
from spb.classes.managers.manager_registry import ManagerRegistry
from tests.support import configurations


def test_privacy_mode_default_comes_from_the_configuration(
    database: Database, tmp_path: Path
) -> None:
    languages = LanguageManager("en")

    private = ManagerRegistry(database, configurations.privacy_mode(tmp_path), languages)
    public = ManagerRegistry(database, configurations.minimal(tmp_path), languages)

    assert private.chat_configurations.default_configuration(-1).privacy_mode
    assert not public.chat_configurations.default_configuration(-1).privacy_mode
