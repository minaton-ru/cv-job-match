"""Decisions: the Candidate's status changes of Vacancies and the list of Assessed Vacancies."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from cv_job_match.constants import ALLOWED_STATUS_CHANGES, RECENT_ASSESSED_LIMIT
from cv_job_match.evaluation import Assessment, Decision, VacancyKey
from cv_job_match.exceptions import (
    RepositoryNotConfiguredError,
    StatusTransitionError,
    VacancyNotFoundError,
)

logger = logging.getLogger(__name__)

VacancyStatus = Literal["new", Decision]


@dataclass(frozen=True)
class AssessedVacancy:
    """A Vacancy with its Assessment and the Candidate's decision about it.

    Attributes:
        fields: The latest known list fields, such as ``title`` and ``url``.
        assessment: Its Assessment.
        status: The Candidate's decision about it, ``new`` until one is made.
    """

    fields: Mapping[str, str | None]
    assessment: Assessment
    status: VacancyStatus


class DecisionRepository(Protocol):
    """Storage of the Candidate's decisions about Vacancies."""

    async def load_status(self, key: VacancyKey) -> VacancyStatus | None:
        """Load the status of one Vacancy.

        Args:
            key: Identity of the Vacancy.

        Returns:
            Its status, or ``None`` when the Vacancy is unknown.
        """
        ...

    async def save_status(self, key: VacancyKey, status: VacancyStatus) -> None:
        """Save the new status of a known Vacancy.

        Args:
            key: Identity of the Vacancy.
            status: Its new status.
        """
        ...

    async def load_recent_assessed(self, limit: int) -> list[AssessedVacancy]:
        """Load the most recently Assessed Vacancies, whatever their score.

        Args:
            limit: How many Vacancies to load at most.

        Returns:
            The Vacancies, the most recently assessed first.
        """
        ...


class Decisions:
    """Moves Vacancies along the statuses the Candidate chooses."""

    def __init__(self, repository: DecisionRepository | None) -> None:
        """Create the Decisions module.

        Args:
            repository: Storage of the Candidate's decisions, if any.
        """
        self.repository = repository

    async def shortlist_vacancy(self, key: VacancyKey) -> None:
        """Mark a Vacancy as shortlisted for Tailoring.

        Args:
            key: Identity of the Vacancy.
        """
        await self.change_status(key, "shortlisted")

    async def mark_applied(self, key: VacancyKey) -> None:
        """Mark a Vacancy as applied to.

        Args:
            key: Identity of the Vacancy.
        """
        await self.change_status(key, "applied")

    async def dismiss_vacancy(self, key: VacancyKey) -> None:
        """Mark a Vacancy as dismissed.

        Args:
            key: Identity of the Vacancy.
        """
        await self.change_status(key, "dismissed")

    async def change_status(self, key: VacancyKey, decision: Decision) -> None:
        """Save the status the Candidate chose for a Vacancy, if the change is allowed.

        Allowed changes are from new to shortlisted, applied or dismissed, and
        from shortlisted to applied or dismissed. Shortlisting a Shortlisted
        Vacancy again is allowed too, so its Tailoring can be retried.

        Args:
            key: Identity of the Vacancy.
            decision: The chosen status.

        Raises:
            RepositoryNotConfiguredError: If the app has no Decision repository.
            VacancyNotFoundError: If the Vacancy is not known.
            StatusTransitionError: If the Vacancy cannot move to the chosen status.
        """
        repository = self.require_repository()
        status = await repository.load_status(key)
        name = describe_vacancy(key)
        logger.debug("Decision %s about %s with status %s", decision, name, status)
        if status is None:
            logger.warning("Decision %s about unknown %s", decision, name)
            raise VacancyNotFoundError(f"{name} is not known.")
        if decision not in ALLOWED_STATUS_CHANGES.get(status, frozenset()):
            message = f"{name} is {status} and cannot become {decision}."
            logger.warning("Refused status change: %s", message)
            raise StatusTransitionError(message)
        await repository.save_status(key, decision)
        logger.info("%s is now %s", name, decision)

    async def list_recent_assessed(self) -> list[AssessedVacancy]:
        """List the most recently Assessed Vacancies, including those below the threshold.

        Returns:
            The Vacancies, the most recently assessed first.

        Raises:
            RepositoryNotConfiguredError: If the app has no Decision repository.
        """
        assessed = await self.require_repository().load_recent_assessed(RECENT_ASSESSED_LIMIT)
        logger.info("Listing %d Assessed Vacancies", len(assessed))
        return assessed

    def require_repository(self) -> DecisionRepository:
        """Return the repository the app may have been built without.

        Returns:
            The Decision repository.

        Raises:
            RepositoryNotConfiguredError: If the app has no Decision repository.
        """
        if self.repository is None:
            logger.error("Decisions have no repository")
            raise RepositoryNotConfiguredError("Decisions need a repository")
        return self.repository


def describe_vacancy(key: VacancyKey) -> str:
    """Name a Vacancy for the Candidate's replies and the logs.

    Args:
        key: Identity of the Vacancy.

    Returns:
        The Vacancy's site ID and Company.
    """
    return f"Vacancy {key.vacancy_id} of {key.company}"
