from pyfederation.enums import IncidentType

from spb.classes.sessions.report_session_manager import ReportSessionManager
from spb.enums import ReportPage
from spb.objects.context.report_context import ReportContext
from tests.support import updates


def open_dialog(sessions: ReportSessionManager) -> ReportContext:
    command = updates.group_message(1, "/report").message
    target = updates.group_message(2, "buy now").message
    return sessions.create(command, target, None, [], False, True)


def test_opens_a_dialog_on_the_incident_page() -> None:
    session = open_dialog(ReportSessionManager())

    assert session.page is ReportPage.INCIDENT
    assert session.target_message_id == 2
    assert session.target_text == "buy now"
    assert session.target_metadata["id"] == 2


def test_only_the_reporter_takes_a_dialog_and_only_once() -> None:
    sessions = ReportSessionManager()
    session = open_dialog(sessions)

    assert sessions.take_owned(session.hash, 99) is None
    assert sessions.take_owned(session.hash, session.reporter_id) == session
    assert sessions.take_owned(session.hash, session.reporter_id) is None


def test_finds_a_dialog_by_its_prompt() -> None:
    sessions = ReportSessionManager()
    session = open_dialog(sessions)

    sessions.update_incident_type(session.hash, IncidentType.SPAM)
    sessions.update_prompt_message_id(session.hash, 10)

    found = sessions.find_by_prompt_message_id(session.chat_id, 10)
    assert found is not None
    assert found.page is ReportPage.COMMENT
    assert found.incident_type is IncidentType.SPAM

    taken = sessions.take_owned_by_prompt_message_id(session.chat_id, 10, session.reporter_id)
    assert taken is not None
    assert sessions.find_by_prompt_message_id(session.chat_id, 10) is None
