"""The application object and the single factory that wires it."""

import logging
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from cv_job_match.adapters.embedded_state import EmbeddedStateAdapter
from cv_job_match.adapters.html_css import HtmlCssAdapter
from cv_job_match.adapters.json_endpoint import JsonEndpointAdapter
from cv_job_match.alerts import send_suspect_run_alerts
from cv_job_match.bot import DecisionBot
from cv_job_match.chat_model import create_chat_model
from cv_job_match.decisions import DecisionRepository, Decisions
from cv_job_match.discovery import (
    Discovery,
    DiscoveryReport,
    DiscoveryRepository,
    TechniqueAdapter,
)
from cv_job_match.evaluation import Assessment, Evaluation, EvaluationRepository, Notifier
from cv_job_match.exceptions import DependencyNotConfiguredError
from cv_job_match.http_client import HttpClient, create_polite_client
from cv_job_match.postgres import (
    PostgresProfileRepository,
    PostgresRepository,
    create_database_engine,
)
from cv_job_match.profile import CandidateProfile, ProfileRepository, load_profile
from cv_job_match.settings import Settings
from cv_job_match.sources import SourcesConfig, Technique, load_sources_config
from cv_job_match.tailoring import Tailoring, TailoringRepository
from cv_job_match.telegram import create_telegram_notifier
from cv_job_match.utils.clock import Clock, SystemClock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Repositories:
    """Repositories for persistence, one per module.

    Attributes:
        discovery: Storage of Scrape Runs and the Vacancies they found.
        profile: Storage of Profile snapshots, if any.
        evaluation: Storage of waiting Vacancies and their Assessments, if any.
        decision: Storage of the Candidate's decisions about Vacancies, if any.
        tailoring: Storage of Shortlisted Vacancies and Application Packs, if any.
    """

    discovery: DiscoveryRepository
    profile: ProfileRepository | None = None
    evaluation: EvaluationRepository | None = None
    decision: DecisionRepository | None = None
    tailoring: TailoringRepository | None = None


@dataclass(frozen=True)
class TickReport:
    """The outcome of one daily pipeline tick.

    Attributes:
        discovery: Every Scrape Run of the tick.
        alert_count: How many alerts about Suspect Scrape Runs were sent.
        assessments: Every Assessment saved in the tick.
    """

    discovery: DiscoveryReport
    alert_count: int
    assessments: tuple[Assessment, ...]


@dataclass(frozen=True)
class App:
    """The wired application.

    ``build_app`` wires the PostgreSQL ``repos`` once the database URL is set,
    the Telegram ``notifier`` once the bot token and chat ID are set, and
    ``llm`` once its model and API key are set; until then each is ``None``. Without ``repos``,
    ``Discovery.run_due`` raises and only ``check_source`` works, and saving a
    Profile snapshot raises too. Evaluation raises without its repository,
    ``llm`` or ``notifier``, and so does the daily tick without ``notifier``.
    Decisions raise without their repository, and Tailoring logs an error
    without its repository, ``llm`` or ``notifier``.

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
        decisions: Moves Vacancies along the statuses the Candidate chooses.
        tailoring: Makes Application Packs for Shortlisted Vacancies.
        bot: Handlers for the Candidate's chat.
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
    decisions: Decisions
    tailoring: Tailoring
    bot: DecisionBot

    async def run_tick(self) -> TickReport:
        """Run the daily pipeline: Discovery, alerts, then Evaluation of waiting Vacancies.

        Every Suspect Scrape Run sends the Candidate an alert. A failed alert is
        logged and does not stop the tick. Evaluation reads the waiting
        Vacancies from the repository, so a Vacancy saved before a crash is
        assessed on the next tick.

        Returns:
            The Scrape Runs, the number of alerts sent and the Assessments of this tick.

        Raises:
            RepositoryNotConfiguredError: If the app has no Discovery or
                Evaluation repository.
            DependencyNotConfiguredError: If the app has no notifier, or
                Evaluation has no chat model.
        """
        logger.info("Starting daily tick")
        notifier = self.require_notifier()
        discovery = await self.discovery.run_due()
        alert_count = await send_suspect_run_alerts(notifier, discovery)
        assessments = await self.evaluation.evaluate_waiting()
        logger.info(
            "Daily tick done: %d Scrape Runs, %d alerts, %d Assessments",
            len(discovery.runs),
            alert_count,
            len(assessments),
        )
        return TickReport(discovery, alert_count, tuple(assessments))

    def require_notifier(self) -> Notifier:
        """Return the notifier the app may have been built without.

        Returns:
            The notifier.

        Raises:
            DependencyNotConfiguredError: If the app has no notifier.
        """
        if self.notifier is None:
            logger.error("Daily tick has no notifier")
            raise DependencyNotConfiguredError("The daily tick needs a notifier")
        return self.notifier


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
    they need to control. Without ``repos``, the PostgreSQL repositories are
    wired when the database URL is set, and without ``notifier``, the Telegram
    notifier is wired when the bot token and the chat ID are set.

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
        TelegramSetupError: If the Telegram bot token is malformed.
    """
    logger.info("Building app")
    sources = load_sources_config(settings.sources_path)
    snapshot = load_profile(settings.profile_path)
    http = http or create_polite_client(settings, sources)
    clock = clock or SystemClock()
    llm = llm if llm is not None else create_chat_model(settings)
    repos = repos or create_postgres_repositories(settings)
    notifier = notifier or create_telegram_notifier(settings, sources)
    adapters: dict[Technique, TechniqueAdapter] = {
        "json_endpoint": JsonEndpointAdapter(http),
        "html_css": HtmlCssAdapter(http),
        "embedded_state": EmbeddedStateAdapter(http),
    }
    discovery = Discovery(sources, adapters, repos.discovery if repos else None, clock)
    decisions = Decisions(repos.decision if repos else None)
    tailoring = Tailoring(
        snapshot, discovery, repos.tailoring if repos else None, llm, notifier, clock
    )
    return App(
        settings=settings,
        sources=sources,
        repos=repos,
        llm=llm,
        http=http,
        notifier=notifier,
        clock=clock,
        discovery=discovery,
        profile=CandidateProfile(snapshot, repos.profile if repos else None),
        evaluation=Evaluation(snapshot, repos.evaluation if repos else None, llm, notifier, clock),
        decisions=decisions,
        tailoring=tailoring,
        bot=DecisionBot(decisions, tailoring, settings.telegram_chat_id),
    )


def create_postgres_repositories(settings: Settings) -> Repositories | None:
    """Create the PostgreSQL repositories, which share one engine.

    Args:
        settings: Settings for this run.

    Returns:
        The repositories, or ``None`` when the database URL is not set.
    """
    if settings.database_url is None:
        logger.info("Database is not configured, so the app has no repositories")
        return None
    engine = create_database_engine(settings.database_url.get_secret_value())
    repository = PostgresRepository(engine)
    return Repositories(
        discovery=repository,
        profile=PostgresProfileRepository(engine),
        evaluation=repository,
        decision=repository,
        tailoring=repository,
    )
