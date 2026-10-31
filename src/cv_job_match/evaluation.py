"""Evaluation: Pre-filter and AI Assessment of waiting Vacancies, and messages about good ones."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from cv_job_match.constants import ASSESSMENT_INSTRUCTIONS, TITLE_FIELD
from cv_job_match.exceptions import (
    DependencyNotConfiguredError,
    InvalidAssessmentError,
    RepositoryNotConfiguredError,
)
from cv_job_match.prefilter import find_prefilter_reason
from cv_job_match.profile import ProfileSnapshot
from cv_job_match.utils.clock import Clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VacancyKey:
    """Identity of one Vacancy.

    Attributes:
        company: The Vacancy's Company.
        vacancy_id: The site's own ID for the Vacancy.
    """

    company: str
    vacancy_id: str


@dataclass(frozen=True)
class WaitingVacancy:
    """A saved Vacancy that is waiting to be assessed.

    Attributes:
        key: Identity of the Vacancy.
        fields: The latest known list fields, such as ``title`` and ``url``.
        description: The full description, or ``None`` when the Source has none.
    """

    key: VacancyKey
    fields: Mapping[str, str | None]
    description: str | None


class AssessmentResult(BaseModel):
    """The structured output the chat model must return.

    Attributes:
        score: How well the Vacancy fits, from 0 to 100.
        verdict: Whether to apply, maybe apply, or skip.
        strengths: The Candidate's strengths that match the Vacancy.
        gaps: What the Vacancy asks for that the Candidate lacks.
        exclusions: The Candidate's exclusions the Vacancy triggers.
    """

    score: Annotated[int, Field(ge=0, le=100)]
    verdict: Literal["apply", "maybe", "skip"]
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Assessment:
    """An AI judgement of how well a Vacancy fits the Candidate Profile.

    Attributes:
        key: Identity of the assessed Vacancy.
        result: Score, verdict, strengths, gaps and exclusions.
        profile_version: Version hash of the Profile it was made against.
        assessed_at: When it was made.
    """

    key: VacancyKey
    result: AssessmentResult
    profile_version: str
    assessed_at: datetime


@dataclass(frozen=True)
class PrefilterRejection:
    """A Vacancy the Pre-filter filtered out, so it never reaches the AI.

    Attributes:
        key: Identity of the filtered-out Vacancy.
        reason: Which rule rejected it and why.
        profile_version: Version hash of the Profile whose rules were applied.
        filtered_at: When it was filtered out.
    """

    key: VacancyKey
    reason: str
    profile_version: str
    filtered_at: datetime


class EvaluationRepository(Protocol):
    """Storage of the Vacancies waiting to be assessed, their Assessments and rejections."""

    async def load_waiting_vacancies(self) -> list[WaitingVacancy]:
        """Load every Vacancy waiting to be assessed.

        Returns:
            Saved Vacancies that are not Baseline or Closed Vacancies, have no
            Assessment and were not filtered out by the Pre-filter.
        """
        ...

    async def load_waiting_vacancy(self, key: VacancyKey) -> WaitingVacancy | None:
        """Load one Vacancy if it is waiting to be assessed.

        Args:
            key: Identity of the Vacancy.

        Returns:
            The Vacancy, or ``None`` when it is unknown or not waiting.
        """
        ...

    async def save_assessment(self, assessment: Assessment) -> None:
        """Save an Assessment, so its Vacancy no longer waits.

        Args:
            assessment: The Assessment to save.
        """
        ...

    async def save_prefilter_rejection(self, rejection: PrefilterRejection) -> None:
        """Save a Pre-filter rejection, so its Vacancy no longer waits.

        Args:
            rejection: The rejection to save.
        """
        ...


class Notifier(Protocol):
    """Sends messages to the Candidate."""

    async def send_message(self, text: str) -> None:
        """Send one message to the Candidate.

        Args:
            text: The message text.
        """
        ...


class Evaluation:
    """Assesses waiting Vacancies and tells the Candidate about good ones."""

    def __init__(
        self,
        profile: ProfileSnapshot,
        repository: EvaluationRepository | None,
        llm: BaseChatModel | None,
        notifier: Notifier | None,
        clock: Clock,
    ) -> None:
        """Create the Evaluation module.

        Args:
            profile: The Candidate Profile loaded at startup.
            repository: Storage of waiting Vacancies and Assessments, if any.
            llm: Chat model that makes Assessments, if configured.
            notifier: Sends messages to the Candidate, if any.
            clock: Source of the current time.
        """
        self.profile = profile
        self.repository = repository
        self.llm = llm
        self.notifier = notifier
        self.clock = clock

    async def evaluate_waiting(self) -> list[Assessment]:
        """Pre-filter and assess every Vacancy waiting to be assessed.

        A Vacancy the Pre-filter rejects is saved as filtered out and makes no
        AI call. A failed AI call or invalid structured output is logged, and the
        Vacancy keeps waiting for the next tick.

        Returns:
            The Assessments saved in this call.

        Raises:
            RepositoryNotConfiguredError: If the app has no Evaluation repository.
            DependencyNotConfiguredError: If the app has no chat model or notifier.
        """
        repository, _, _ = self.require_dependencies()
        waiting = await repository.load_waiting_vacancies()
        logger.info("Evaluating %d waiting Vacancies", len(waiting))
        assessments = [await self.assess_waiting_vacancy(vacancy) for vacancy in waiting]
        return [assessment for assessment in assessments if assessment is not None]

    async def evaluate_vacancy(self, key: VacancyKey) -> Assessment | None:
        """Assess one Vacancy if it is waiting to be assessed.

        Args:
            key: Identity of the Vacancy.

        Returns:
            The saved Assessment, or ``None`` when the Vacancy is not waiting,
            the Pre-filter rejected it or its Assessment failed.

        Raises:
            RepositoryNotConfiguredError: If the app has no Evaluation repository.
            DependencyNotConfiguredError: If the app has no chat model or notifier.
        """
        repository, _, _ = self.require_dependencies()
        vacancy = await repository.load_waiting_vacancy(key)
        if vacancy is None:
            logger.info("Vacancy %s of %s is not waiting", key.vacancy_id, key.company)
            return None
        return await self.assess_waiting_vacancy(vacancy)

    async def assess_waiting_vacancy(self, vacancy: WaitingVacancy) -> Assessment | None:
        """Pre-filter and assess one waiting Vacancy, save the outcome and send a good match.

        Args:
            vacancy: The waiting Vacancy.

        Returns:
            The saved Assessment, or ``None`` when the Pre-filter rejected the
            Vacancy or the AI call or its output failed.

        Raises:
            RepositoryNotConfiguredError: If the app has no Evaluation repository.
            DependencyNotConfiguredError: If the app has no chat model or notifier.
        """
        repository, llm, notifier = self.require_dependencies()
        key = vacancy.key
        reason = find_prefilter_reason(self.profile.criteria.prefilter, vacancy.fields)
        if reason is not None:
            rejection = PrefilterRejection(key, reason, self.profile.version, self.clock.now_utc())
            await repository.save_prefilter_rejection(rejection)
            logger.info("Filtered out Vacancy %s of %s: %s", key.vacancy_id, key.company, reason)
            return None
        try:
            result = await request_assessment(llm, self.profile, vacancy)
        except Exception as error:
            logger.warning(
                "Assessment of Vacancy %s of %s failed, it keeps waiting: %s",
                key.vacancy_id,
                key.company,
                error,
                exc_info=True,
            )
            return None
        assessment = Assessment(key, result, self.profile.version, self.clock.now_utc())
        await repository.save_assessment(assessment)
        logger.info(
            "Assessed Vacancy %s of %s: score %d, verdict %s",
            key.vacancy_id,
            key.company,
            result.score,
            result.verdict,
        )
        logger.debug("Assessment of Vacancy %s: %s", key.vacancy_id, result.model_dump())
        if result.score >= self.profile.criteria.score_threshold:
            await send_assessment_message(notifier, vacancy, result)
        return assessment

    def require_dependencies(self) -> tuple[EvaluationRepository, BaseChatModel, Notifier]:
        """Return the dependencies the app may have been built without.

        Returns:
            The repository, the chat model and the notifier.

        Raises:
            RepositoryNotConfiguredError: If the app has no Evaluation repository.
            DependencyNotConfiguredError: If the app has no chat model or notifier.
        """
        if self.repository is None:
            logger.error("Evaluation has no repository")
            raise RepositoryNotConfiguredError("Evaluation needs a repository")
        for dependency, name in ((self.llm, "chat model"), (self.notifier, "notifier")):
            if dependency is None:
                logger.error("Evaluation has no %s", name)
                raise DependencyNotConfiguredError(f"Evaluation needs a {name}")
        return self.repository, self.llm, self.notifier


async def send_assessment_message(
    notifier: Notifier, vacancy: WaitingVacancy, result: AssessmentResult
) -> None:
    """Send a good match to the Candidate, logging a failed send instead of raising it.

    The Assessment is already saved, so a failed send is not retried and the
    tick goes on with the next Vacancy.

    Args:
        notifier: Sends messages to the Candidate.
        vacancy: The assessed Vacancy.
        result: Its Assessment result.
    """
    key = vacancy.key
    try:
        await notifier.send_message(format_assessment_message(vacancy, result))
    except Exception as error:
        logger.error(
            "Assessed Vacancy %s of %s, but the message was not sent: %s",
            key.vacancy_id,
            key.company,
            error,
            exc_info=True,
        )
        return
    logger.info("Sent Vacancy %s of %s to the Candidate", key.vacancy_id, key.company)


async def request_assessment(
    llm: BaseChatModel, profile: ProfileSnapshot, vacancy: WaitingVacancy
) -> AssessmentResult:
    """Ask the chat model for an Assessment with structured output.

    Args:
        llm: The chat model.
        profile: The Candidate Profile.
        vacancy: The Vacancy to assess.

    Returns:
        The validated Assessment result.

    Raises:
        InvalidAssessmentError: If the model returns no structured output.
        Exception: Whatever the chat model or output validation raises.
    """
    model = llm.with_structured_output(AssessmentResult, method="function_calling")
    messages = [
        SystemMessage(build_profile_prompt(profile)),
        HumanMessage(build_vacancy_prompt(vacancy)),
    ]
    logger.debug("Requesting Assessment of Vacancy %s", vacancy.key.vacancy_id)
    result = await model.ainvoke(messages)
    if not isinstance(result, AssessmentResult):
        logger.error("Chat model returned no Assessment: %r", result)
        raise InvalidAssessmentError(f"chat model returned no Assessment: {result!r}")
    return result


def build_profile_prompt(profile: ProfileSnapshot) -> str:
    """Build the system prompt that describes the Candidate.

    Args:
        profile: The Candidate Profile.

    Returns:
        The assessment instructions with the resume and criteria.
    """
    criteria = profile.criteria
    return ASSESSMENT_INSTRUCTIONS.format(
        resume=profile.resume,
        preferences=join_items(criteria.preferences),
        excluded_roles=join_items(criteria.excluded_roles),
        strengths=join_items(criteria.strengths),
        weaknesses=join_items(criteria.weaknesses),
    )


def build_vacancy_prompt(vacancy: WaitingVacancy) -> str:
    """Build the user prompt that describes the Vacancy.

    Args:
        vacancy: The Vacancy to assess.

    Returns:
        The Company, list fields and description of the Vacancy.
    """
    lines = [f"Company: {vacancy.key.company}"]
    lines += [f"{name}: {value}" for name, value in vacancy.fields.items() if value is not None]
    lines.append(f"\nDescription:\n{vacancy.description or 'none'}")
    return "\n".join(lines)


def format_assessment_message(vacancy: WaitingVacancy, result: AssessmentResult) -> str:
    """Build the message that tells the Candidate about a good match.

    Args:
        vacancy: The assessed Vacancy.
        result: Its Assessment result.

    Returns:
        Company, title, link, score, verdict and short reasons.
    """
    title = vacancy.fields.get(TITLE_FIELD) or vacancy.key.vacancy_id
    return "\n".join(
        [
            f"{vacancy.key.company}: {title}",
            vacancy.fields.get("url") or "no link",
            f"Score {result.score}, verdict: {result.verdict}",
            f"Strengths: {join_items(result.strengths)}",
            f"Gaps: {join_items(result.gaps)}",
            f"Exclusions: {join_items(result.exclusions)}",
        ]
    )


def join_items(items: list[str]) -> str:
    """Join a list of short texts for a prompt or message.

    Args:
        items: The texts.

    Returns:
        The texts separated by semicolons, or ``none`` for an empty list.
    """
    return "; ".join(items) or "none"
