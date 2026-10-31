"""The application object and the single factory that wires it."""

import logging
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from cv_job_match.adapters.embedded_state import EmbeddedStateAdapter
from cv_job_match.adapters.html_css import HtmlCssAdapter
from cv_job_match.adapters.json_endpoint import JsonEndpointAdapter
from cv_job_match.chat_model import create_chat_model
from cv_job_match.discovery import (
    Discovery,
    DiscoveryReport,
    DiscoveryRepository,
    TechniqueAdapter,
)
from cv_job_match.evaluation import Assessment, Evaluation, EvaluationRepository, Notifier
from cv_job_match.http_client import HttpClient, create_polite_client
from cv_job_match.profile import CandidateProfile, ProfileRepository, load_profile
from cv_job_match.settings import Settings
from cv_job_match.sources import SourcesConfig, Technique, load_sources_config
from cv_job_match.utils.clock import Clock, SystemClock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Repositories:
    """Repositories for persistence, one per module.

    Attributes:
        discovery: Storage of Scrape Runs and the Vacancies they found.
        profile: Storage of Profile snapshots, if any.
        evaluation: Storage of waiting Vacancies and their Assessments, if any.
    """

    discovery: DiscoveryRepository
    profile: ProfileRepository | None = None
    evaluation: EvaluationRepository | None = None


@dataclass(frozen=True)
class TickReport:
    """The outcome of one daily pipeline tick.

    Attributes:
        discovery: Every Scrape Run of the tick.
        assessments: Every Assessment saved in the tick.
    """

    discovery: DiscoveryReport
    assessments: tuple[Assessment, ...]


@dataclass(frozen=True)
class App:
    """The wired application.

    The ``repos`` and ``notifier`` dependencies have no production
    implementation yet, so they stay ``None`` in production, and ``llm`` is
    ``None`` until its model and API key are set. Without ``repos``,
    ``Discovery.run_due`` raises and only ``check_source`` works, and saving a
    Profile snapshot raises too. Evaluation raises without its repository,
    ``llm`` or ``notifier``.

    Attributes:
        settings: Settings the app was built with.
        sources: The validated Sources config.
        repos: Repositories for persistence, one per module.
        llm: Chat model for Assessment and Tailoring.
        http: HTTP client for the careers sites.
        notifier: Sends messages to the Candidate.
        clock: Source of the current time.
        discovery: Checks Sources for Vacancies.
        profile: The Candidate Profile loaded at startup.
        evaluation: Assesses waiting Vacancies and sends good matches.
    """

    settings: Settings
    sources: SourcesConfig
    repos: Repositories | None
    llm: BaseChatModel | None
    http: HttpClient
    notifier: Notifier | None
    clock: Clock
    discovery: Discovery
    profile: CandidateProfile
    evaluation: Evaluation

    async def run_tick(self) -> TickReport:
        """Run the daily pipeline: Discovery, then Evaluation of waiting Vacancies.

        Evaluation reads the waiting Vacancies from the repository, so a
        Vacancy saved before a crash is assessed on the next tick.

        Returns:
            The Scrape Runs and the Assessments of this tick.

        Raises:
            RepositoryNotConfiguredError: If the app has no Discovery or
                Evaluation repository.
            DependencyNotConfiguredError: If Evaluation has no chat model or notifier.
        """
        logger.info("Starting daily tick")
        discovery = await self.discovery.run_due()
        assessments = await self.evaluation.evaluate_waiting()
        logger.info(
            "Daily tick done: %d Scrape Runs, %d Assessments",
            len(discovery.runs),
            len(assessments),
        )
        return TickReport(discovery, tuple(assessments))


def build_app(
    settings: Settings,
    *,
    repos: Repositories | None = None,
    llm: BaseChatModel | None = None,
    http: HttpClient | None = None,
    notifier: Notifier | None = None,
    clock: Clock | None = None,
) -> App:
    """Build the application from settings.

    Production passes only ``settings``. Tests pass a fake for any dependency
    they need to control.

    Args:
        settings: Settings for this run.
        repos: Repositories to use instead of the production ones.
        llm: Chat model to use instead of the production one.
        http: HTTP client to use instead of the production one.
        notifier: Notifier to use instead of the production one.
        clock: Clock to use instead of the system clock.

    Returns:
        The wired application.

    Raises:
        SourcesConfigError: If the Sources config is missing or invalid.
        ProfileError: If the Profile is missing or invalid.
        HttpClientSetupError: If the extra root certificate cannot be loaded.
    """
    logger.info("Building app")
    sources = load_sources_config(settings.sources_path)
    snapshot = load_profile(settings.profile_path)
    http = http or create_polite_client(settings, sources)
    clock = clock or SystemClock()
    llm = llm if llm is not None else create_chat_model(settings)
    adapters: dict[Technique, TechniqueAdapter] = {
        "json_endpoint": JsonEndpointAdapter(http),
        "html_css": HtmlCssAdapter(http),
        "embedded_state": EmbeddedStateAdapter(http),
    }
    return App(
        settings=settings,
        sources=sources,
        repos=repos,
        llm=llm,
        http=http,
        notifier=notifier,
        clock=clock,
        discovery=Discovery(sources, adapters, repos.discovery if repos else None, clock),
        profile=CandidateProfile(snapshot, repos.profile if repos else None),
        evaluation=Evaluation(snapshot, repos.evaluation if repos else None, llm, notifier, clock),
    )
