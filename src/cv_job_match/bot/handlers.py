"""Bot handlers for the Candidate's chat, independent of any chat library."""

import logging

from cv_job_match.constants import TITLE_FIELD
from cv_job_match.decisions import AssessedVacancy, Decision, Decisions, describe_vacancy
from cv_job_match.evaluation import VacancyKey
from cv_job_match.exceptions import StatusTransitionError, VacancyNotFoundError
from cv_job_match.tailoring import Tailoring

logger = logging.getLogger(__name__)


class DecisionBot:
    """Thin handlers that answer the configured chat and delegate to Decisions and Tailoring."""

    def __init__(self, decisions: Decisions, tailoring: Tailoring, chat_id: int | None) -> None:
        """Create the bot handlers.

        Args:
            decisions: Changes Vacancy statuses.
            tailoring: Makes the Application Pack of a Shortlisted Vacancy.
            chat_id: The only chat the bot answers, or ``None`` to answer none.
        """
        self.decisions = decisions
        self.tailoring = tailoring
        self.chat_id = chat_id

    async def handle_button(self, chat_id: int, decision: Decision, key: VacancyKey) -> str | None:
        """Apply the decision of a pressed Vacancy button.

        Shortlisting, again too, starts Tailoring in the background.

        Args:
            chat_id: The chat the button was pressed in.
            decision: The status the button moves the Vacancy to.
            key: Identity of the Vacancy.

        Returns:
            The reply to the Candidate, or ``None`` for any other chat.
        """
        if not self.is_configured_chat(chat_id):
            return None
        logger.debug("Button %s pressed for %s", decision, describe_vacancy(key))
        handlers = {
            "shortlisted": self.decisions.shortlist_vacancy,
            "applied": self.decisions.mark_applied,
            "dismissed": self.decisions.dismiss_vacancy,
        }
        try:
            await handlers[decision](key)
        except (VacancyNotFoundError, StatusTransitionError) as error:
            return str(error)
        if decision == "shortlisted":
            self.tailoring.start_tailoring(key)
        logger.info("Confirmed button %s for %s", decision, describe_vacancy(key))
        return f"{describe_vacancy(key)} is now {decision}."

    async def handle_list_command(self, chat_id: int) -> str | None:
        """Answer ``/list`` with the recent Assessed Vacancies, below the threshold too.

        Args:
            chat_id: The chat the command came from.

        Returns:
            The reply to the Candidate, or ``None`` for any other chat.
        """
        if not self.is_configured_chat(chat_id):
            return None
        assessed = await self.decisions.list_recent_assessed()
        logger.info("Answering /list with %d Assessed Vacancies", len(assessed))
        if not assessed:
            return "No Assessed Vacancies yet."
        return "\n".join(["Recent Assessed Vacancies:", *map(format_assessed_line, assessed)])

    def is_configured_chat(self, chat_id: int) -> bool:
        """Check whether an update comes from the configured chat, logging one that does not.

        Args:
            chat_id: The chat the update came from.

        Returns:
            Whether the bot answers this chat.
        """
        if chat_id == self.chat_id:
            return True
        logger.warning("Ignored an update from chat %d", chat_id)
        return False


def format_assessed_line(vacancy: AssessedVacancy) -> str:
    """Build one ``/list`` line about an Assessed Vacancy.

    Args:
        vacancy: The Assessed Vacancy.

    Returns:
        Company, title, score, verdict and status.
    """
    key = vacancy.assessment.key
    result = vacancy.assessment.result
    title = vacancy.fields.get(TITLE_FIELD) or key.vacancy_id
    return f"{key.company}: {title}, score {result.score}, {result.verdict}, {vacancy.status}"
