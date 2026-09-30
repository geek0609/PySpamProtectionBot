"""The short-lived dialogs the bot keeps open."""

from dataclasses import dataclass, field

from spb.classes.sessions.configuration_session_manager import ConfigurationSessionManager
from spb.classes.sessions.report_action_tracker import ReportActionTracker
from spb.classes.sessions.report_session_manager import ReportSessionManager
from spb.classes.sessions.secretary_report_store import SecretaryReportStore


@dataclass(frozen=True, slots=True)
class SessionRegistry:
    """The short-lived dialogs the bot keeps open while a user works through a multi-step flow.

    Each kind of dialog has its own manager because each expires on its own schedule and is keyed
    differently. Bundling them means a handler reaches the one it needs through the context without
    the context growing a field per dialog.

    Attributes:
        configuration: Configuration sessions opened by /start or /settings in a group.
        report: Report dialogs opened by /report.
        report_actions: Which moderators' report notifications carry buttons, and whether one was
            used.
        secretary_reports: First-contact messages a secretary notification offers to report.
    """

    configuration: ConfigurationSessionManager = field(default_factory=ConfigurationSessionManager)
    report: ReportSessionManager = field(default_factory=ReportSessionManager)
    report_actions: ReportActionTracker = field(default_factory=ReportActionTracker)
    secretary_reports: SecretaryReportStore = field(default_factory=SecretaryReportStore)
