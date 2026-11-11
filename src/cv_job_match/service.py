"""The long-running service: the Telegram bot and the daily scheduler in one event loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from pydantic import SecretStr

from cv_job_match.app import App
from cv_job_match.bot.telegram import TelegramNotifier, create_dispatcher
from cv_job_match.constants import REQUIRED_SERVICE_SETTINGS, SCHEDULER_INTERVAL_S
from cv_job_match.exceptions import DependencyNotConfiguredError, MissingSecretError
from cv_job_match.settings import Settings

logger = logging.getLogger(__name__)


def check_required_secrets(settings: Settings) -> None:
    """Check that every secret the service needs is set.

    Args:
        settings: Settings for this run.

    Raises:
        MissingSecretError: If any secret is missing, empty or whitespace-only;
            every missing variable is named.
    """
    prefix = str(Settings.model_config.get("env_prefix", ""))
    missing = [
        f"{prefix}{name}".upper()
        for name in REQUIRED_SERVICE_SETTINGS
        if is_blank_setting(getattr(settings, name))
    ]
    if missing:
        message = f"Missing secrets: {', '.join(missing)}"
        logger.error("%s", message)
        raise MissingSecretError(message)


def is_blank_setting(value: object) -> bool:
    """Check whether a setting holds no value.

    Args:
        value: The setting's value, plain or secret.

    Returns:
        Whether the value is ``None``, empty or whitespace-only.
    """
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    return value is None or (isinstance(value, str) and not value.strip())


async def run_scheduler(
    tick: Callable[[], Awaitable[object]],
    interval_s: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run the tick, then sleep for the interval, forever.

    A failed tick is logged and does not stop the scheduler.

    Args:
        tick: The daily pipeline.
        interval_s: Seconds between the end of one tick and the start of the next.
        sleep: Waits for a number of seconds. Tests pass a fake.
    """
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("Daily tick failed")
        logger.debug("Next tick in %s seconds", interval_s)
        await sleep(interval_s)


def require_telegram_bot(app: App) -> Bot:
    """Return the aiogram bot of the app's Telegram notifier.

    Args:
        app: The built application.

    Returns:
        The bot.

    Raises:
        DependencyNotConfiguredError: If the app has no Telegram notifier.
    """
    notifier = app.notifier
    if isinstance(notifier, TelegramNotifier) and isinstance(notifier.bot, Bot):
        return notifier.bot
    logger.error("The service has no Telegram notifier")
    raise DependencyNotConfiguredError("The service needs a Telegram notifier")


async def run_service(app: App) -> None:
    """Run the Telegram bot and the daily scheduler until the bot stops.

    Args:
        app: The application built with every production dependency.

    Raises:
        DependencyNotConfiguredError: If the app has no Telegram notifier.
    """
    bot = require_telegram_bot(app)
    await app.profile.save_snapshot_if_new()
    scheduler = asyncio.create_task(run_scheduler(app.run_tick, SCHEDULER_INTERVAL_S))
    logger.info("Service started")
    try:
        await create_dispatcher(app.bot, app.sources).start_polling(bot)
    finally:
        scheduler.cancel()
        await app.tailoring.wait_for_background_tasks()
        logger.info("Service stopped")
