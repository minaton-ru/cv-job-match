"""Tailoring: the Application Pack for a Shortlisted Vacancy, made in the background."""

import asyncio
import io
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from markdown_pdf import MarkdownPdf, Section
from pydantic import BaseModel, Field

from cv_job_match.constants import (
    RESUME_PAPER_SIZE,
    RESUME_PDF_CSS,
    TAILORED_RESUME_MARKDOWN_NAME,
    TAILORED_RESUME_PDF_NAME,
    TAILORING_INSTRUCTIONS,
)
from cv_job_match.decisions import describe_vacancy
from cv_job_match.discovery import Discovery
from cv_job_match.evaluation import Notifier, VacancyKey, build_vacancy_prompt
from cv_job_match.exceptions import (
    DependencyNotConfiguredError,
    InvalidTailoringError,
    RepositoryNotConfiguredError,
    SourceFetchError,
    SourcesConfigError,
)
from cv_job_match.profile import ProfileSnapshot
from cv_job_match.utils.clock import Clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShortlistedVacancy:
    """A Shortlisted Vacancy with what Tailoring needs to know about it.

    Attributes:
        key: Identity of the Vacancy.
        source_id: The Source that recorded the Vacancy.
        fields: The latest known list fields, such as ``title`` and ``url``.
        description: The description saved at discovery, or ``None`` when it has none.
    """

    key: VacancyKey
    source_id: str
    fields: Mapping[str, str | None]
    description: str | None


class TailoringResult(BaseModel):
    """The structured output the chat model must return.

    Attributes:
        resume_markdown: The tailored resume in Markdown.
        cover_letter: The cover letter as plain text.
    """

    resume_markdown: str = Field(min_length=1)
    cover_letter: str = Field(min_length=1)


@dataclass(frozen=True)
class ApplicationPack:
    """Metadata of an Application Pack sent to the Candidate.

    Attributes:
        key: Identity of the tailored Vacancy.
        profile_version: Version hash of the Profile it was made from.
        language: The language it is written in.
        tailored_at: When it was made.
    """

    key: VacancyKey
    profile_version: str
    language: str
    tailored_at: datetime


class TailoringRepository(Protocol):
    """Storage of Shortlisted Vacancies and the Application Packs made for them."""

    async def load_shortlisted_vacancy(self, key: VacancyKey) -> ShortlistedVacancy | None:
        """Load one Vacancy if it is shortlisted.

        Args:
            key: Identity of the Vacancy.

        Returns:
            The Vacancy, or ``None`` when it is unknown or not shortlisted.
        """
        ...

    async def save_application_pack(self, pack: ApplicationPack) -> None:
        """Save the metadata of an Application Pack.

        Args:
            pack: The metadata to save.
        """
        ...


class Tailoring:
    """Makes Application Packs for Shortlisted Vacancies and sends them to the Candidate."""

    def __init__(
        self,
        profile: ProfileSnapshot,
        discovery: Discovery,
        repository: TailoringRepository | None,
        llm: BaseChatModel | None,
        notifier: Notifier | None,
        clock: Clock,
    ) -> None:
        """Create the Tailoring module.

        Args:
            profile: The Candidate Profile loaded at startup.
            discovery: Fetches a Vacancy's description again.
            repository: Storage of Shortlisted Vacancies and Application Packs, if any.
            llm: Chat model that tailors the resume and cover letter, if configured.
            notifier: Sends the Application Pack to the Candidate, if any.
            clock: Source of the current time.
        """
        self.profile = profile
        self.discovery = discovery
        self.repository = repository
        self.llm = llm
        self.notifier = notifier
        self.clock = clock
        self.tasks: dict[VacancyKey, asyncio.Task[None]] = {}

    def start_tailoring(self, key: VacancyKey) -> None:
        """Start Tailoring of a Vacancy as a background task, unless one is already running.

        Args:
            key: Identity of the Shortlisted Vacancy.
        """
        if key in self.tasks:
            logger.info("Tailoring of %s is already running", describe_vacancy(key))
            return
        task = asyncio.create_task(self.run_tailoring_task(key))
        self.tasks[key] = task
        task.add_done_callback(lambda _: self.tasks.pop(key, None))
        logger.info("Started Tailoring of %s", describe_vacancy(key))

    async def wait_for_background_tasks(self) -> None:
        """Wait until every started Tailoring is done."""
        await asyncio.gather(*self.tasks.values())

    async def run_tailoring_task(self, key: VacancyKey) -> None:
        """Run Tailoring, logging any error, since nothing awaits a background task.

        Args:
            key: Identity of the Shortlisted Vacancy.
        """
        try:
            await self.tailor_application(key)
        except Exception as error:
            logger.error(
                "Tailoring of %s stopped: %s", describe_vacancy(key), error, exc_info=True
            )

    async def tailor_application(self, key: VacancyKey) -> ApplicationPack | None:
        """Make the Application Pack for a Shortlisted Vacancy and send it to the Candidate.

        The description is fetched again first. A failed fetch or AI call sends
        the Candidate a failure message, and shortlisting again retries.

        Args:
            key: Identity of the Vacancy.

        Returns:
            The saved Application Pack metadata, or ``None`` when the Vacancy is
            not shortlisted or Tailoring failed.

        Raises:
            RepositoryNotConfiguredError: If the app has no Tailoring repository.
            DependencyNotConfiguredError: If the app has no chat model or notifier.
        """
        repository, llm, notifier = self.require_dependencies()
        name = describe_vacancy(key)
        vacancy = await repository.load_shortlisted_vacancy(key)
        if vacancy is None:
            logger.warning("Tailoring skipped: %s is not shortlisted", name)
            return None
        try:
            description = await self.refresh_description(vacancy)
        except (SourceFetchError, SourcesConfigError, LookupError) as error:
            await report_failure(
                notifier, key, "the description could not be fetched again", error
            )
            return None
        try:
            result = await request_tailoring(llm, self.profile, vacancy, description)
        except Exception as error:
            await report_failure(notifier, key, "the AI call failed", error)
            return None
        logger.debug("Tailoring result of %s: %s", name, result.model_dump())
        pdf = await asyncio.to_thread(render_resume_pdf, result.resume_markdown)
        await notifier.send_document(TAILORED_RESUME_PDF_NAME, pdf)
        await notifier.send_document(
            TAILORED_RESUME_MARKDOWN_NAME, result.resume_markdown.encode("utf-8")
        )
        await notifier.send_message(result.cover_letter)
        pack = ApplicationPack(
            key, self.profile.version, self.profile.criteria.language, self.clock.now_utc()
        )
        await repository.save_application_pack(pack)
        logger.info("Sent Application Pack for %s", name)
        return pack

    async def refresh_description(self, vacancy: ShortlistedVacancy) -> str | None:
        """Fetch the Vacancy's description again, keeping the saved one when its Source has none.

        Args:
            vacancy: The Shortlisted Vacancy.

        Returns:
            The current description, the saved one, or ``None`` when there is neither.

        Raises:
            SourceFetchError: If the description cannot be fetched or read.
            SourcesConfigError: If the Source's technique options are invalid.
            LookupError: If the Source or its technique adapter is gone.
        """
        description = await self.discovery.fetch_current_description(
            vacancy.source_id, vacancy.key.vacancy_id, vacancy.fields
        )
        return vacancy.description if description is None else description

    def require_dependencies(self) -> tuple[TailoringRepository, BaseChatModel, Notifier]:
        """Return the dependencies the app may have been built without.

        Returns:
            The repository, the chat model and the notifier.

        Raises:
            RepositoryNotConfiguredError: If the app has no Tailoring repository.
            DependencyNotConfiguredError: If the app has no chat model or notifier.
        """
        if self.repository is None:
            logger.error("Tailoring has no repository")
            raise RepositoryNotConfiguredError("Tailoring needs a repository")
        for dependency, name in ((self.llm, "chat model"), (self.notifier, "notifier")):
            if dependency is None:
                logger.error("Tailoring has no %s", name)
                raise DependencyNotConfiguredError(f"Tailoring needs a {name}")
        return self.repository, self.llm, self.notifier


async def report_failure(
    notifier: Notifier, key: VacancyKey, reason: str, error: Exception
) -> None:
    """Log a failed Tailoring and tell the Candidate that shortlisting again retries.

    Args:
        notifier: Sends messages to the Candidate.
        key: Identity of the Vacancy.
        reason: What failed, in the Candidate's words.
        error: The error behind it.
    """
    name = describe_vacancy(key)
    logger.warning("Tailoring of %s failed, %s: %s", name, reason, error, exc_info=True)
    await notifier.send_message(
        f"Tailoring of {name} failed: {reason}. Press Shortlist to try again."
    )


async def request_tailoring(
    llm: BaseChatModel,
    profile: ProfileSnapshot,
    vacancy: ShortlistedVacancy,
    description: str | None,
) -> TailoringResult:
    """Ask the chat model for the tailored resume and cover letter with structured output.

    Args:
        llm: The chat model.
        profile: The Candidate Profile.
        vacancy: The Vacancy to tailor for.
        description: Its current description.

    Returns:
        The validated Tailoring result.

    Raises:
        InvalidTailoringError: If the model returns no structured output.
        Exception: Whatever the chat model or output validation raises.
    """
    model = llm.with_structured_output(TailoringResult, method="function_calling")
    instructions = TAILORING_INSTRUCTIONS.format(
        language=profile.criteria.language, resume=profile.resume
    )
    messages = [
        SystemMessage(instructions),
        HumanMessage(build_vacancy_prompt(vacancy.key, vacancy.fields, description)),
    ]
    logger.debug("Requesting Tailoring of Vacancy %s", vacancy.key.vacancy_id)
    result = await model.ainvoke(messages)
    if not isinstance(result, TailoringResult):
        logger.error("Chat model returned no Application Pack: %r", result)
        raise InvalidTailoringError(f"chat model returned no Application Pack: {result!r}")
    return result


def render_resume_pdf(resume_markdown: str) -> bytes:
    """Render the tailored resume to PDF with the fixed template.

    Args:
        resume_markdown: The tailored resume in Markdown.

    Returns:
        The PDF file's bytes.
    """
    pdf = MarkdownPdf(toc_level=0)
    pdf.add_section(
        Section(resume_markdown, toc=False, paper_size=RESUME_PAPER_SIZE), user_css=RESUME_PDF_CSS
    )
    buffer = io.BytesIO()
    pdf.save_bytes(buffer)
    return buffer.getvalue()
