"""The application object and the single factory that wires it."""

import logging
from dataclasses import dataclass

from cv_job_match.settings import Settings
from cv_job_match.sources import SourcesConfig, load_sources_config
from cv_job_match.utils.clock import Clock, SystemClock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class App:
    """The wired application.

    The ``repos``, ``llm``, ``http`` and ``notifier`` dependencies are not
    built yet. They stay ``None`` in production until the modules that use
    them define their interfaces and production implementations.

    Attributes:
        settings: Settings the app was built with.
        sources: The validated Sources config.
        repos: Repositories for persistence.
        llm: Chat model for Assessment and Tailoring.
        http: HTTP client for the careers sites.
        notifier: Sends messages to the Candidate.
        clock: Source of the current time.
    """

    settings: Settings
    sources: SourcesConfig
    repos: object | None
    llm: object | None
    http: object | None
    notifier: object | None
    clock: Clock


def build_app(
    settings: Settings,
    *,
    repos: object | None = None,
    llm: object | None = None,
    http: object | None = None,
    notifier: object | None = None,
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
    """
    logger.info("Building app")
    sources = load_sources_config(settings.sources_path)
    return App(
        settings=settings,
        sources=sources,
        repos=repos,
        llm=llm,
        http=http,
        notifier=notifier,
        clock=clock or SystemClock(),
    )
