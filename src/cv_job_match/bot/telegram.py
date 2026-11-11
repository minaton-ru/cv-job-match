"""The real Notifier and chat handlers on top of the aiogram Telegram library."""

import logging
from collections.abc import Sequence
from typing import Protocol

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.token import TokenValidationError

from cv_job_match.bot.handlers import DecisionBot
from cv_job_match.constants import LIST_COMMAND, UNKNOWN_BUTTON_REPLY, VACANCY_CALLBACK_PREFIX
from cv_job_match.evaluation import Decision, DecisionButton, VacancyKey
from cv_job_match.exceptions import TelegramSetupError, UnknownSourceError
from cv_job_match.settings import Settings
from cv_job_match.sources import SourcesConfig

logger = logging.getLogger(__name__)


class VacancyCallback(CallbackData, prefix=VACANCY_CALLBACK_PREFIX):
    """Data of a Vacancy button.

    Telegram allows only 64 bytes of button data, so the Vacancy's Company is
    named by the id of one of its Sources instead of by the Company itself.

    Attributes:
        decision: The status the button moves the Vacancy to.
        source_id: Id of a Source of the Vacancy's Company.
        vacancy_id: The site's own ID for the Vacancy.
    """

    decision: Decision
    source_id: str
    vacancy_id: str


class TelegramBot(Protocol):
    """The part of ``aiogram.Bot`` the notifier uses."""

    async def send_message(
        self, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
    ) -> object:
        """Send one text message.

        Args:
            chat_id: The chat to send to.
            text: The message text.
            reply_markup: Buttons under the message, if any.
        """
        ...

    async def send_document(self, chat_id: int, document: BufferedInputFile) -> object:
        """Send one file.

        Args:
            chat_id: The chat to send to.
            document: The file.
        """
        ...


class TelegramNotifier:
    """Sends messages and files to the Candidate's Telegram chat."""

    def __init__(self, bot: TelegramBot, chat_id: int, sources: SourcesConfig) -> None:
        """Create the notifier.

        Args:
            bot: The Telegram bot that sends.
            chat_id: The Candidate's chat.
            sources: The Sources config, used to name Companies in buttons.
        """
        self.bot = bot
        self.chat_id = chat_id
        self.sources = sources

    async def send_message(self, text: str, buttons: Sequence[DecisionButton] = ()) -> None:
        """Send one message to the Candidate, with its buttons in one row.

        Args:
            text: The message text.
            buttons: Buttons shown under the message, if any.

        Raises:
            UnknownSourceError: If a button's Company has no Source in the config.
            ValueError: If a button's data does not fit into Telegram's limit.
        """
        markup = None
        if buttons:
            row = [
                InlineKeyboardButton(text=button.label, callback_data=self.pack_button(button))
                for button in buttons
            ]
            markup = InlineKeyboardMarkup(inline_keyboard=[row])
        logger.debug("Sending a message with %d buttons: %s", len(buttons), text)
        await self.bot.send_message(self.chat_id, text, reply_markup=markup)

    async def send_document(self, file_name: str, content: bytes) -> None:
        """Send one file to the Candidate.

        Args:
            file_name: The name the file is shown with.
            content: The file's bytes.
        """
        logger.debug("Sending file %s of %d bytes", file_name, len(content))
        await self.bot.send_document(self.chat_id, BufferedInputFile(content, file_name))

    def pack_button(self, button: DecisionButton) -> str:
        """Build the data of a Vacancy button.

        Args:
            button: The button.

        Returns:
            The packed ``VacancyCallback``.

        Raises:
            UnknownSourceError: If the button's Company has no Source in the config.
            ValueError: If the data does not fit into Telegram's limit.
        """
        source_id = find_company_source_id(self.sources, button.key.company)
        return VacancyCallback(
            decision=button.decision, source_id=source_id, vacancy_id=button.key.vacancy_id
        ).pack()


def find_company_source_id(sources: SourcesConfig, company: str) -> str:
    """Find the id of the first Source of a Company.

    Args:
        sources: The Sources config.
        company: The Company.

    Returns:
        The Source id.

    Raises:
        UnknownSourceError: If the Company has no Source in the config.
    """
    for source in sources.sources:
        if source.company == company:
            return source.id
    logger.error("Company %r has no Source in the config", company)
    raise UnknownSourceError(f"Company {company} has no Source in the config")


def parse_vacancy_callback(
    callback: VacancyCallback, sources: SourcesConfig
) -> tuple[Decision, VacancyKey] | None:
    """Read the decision and the Vacancy of a pressed button.

    Args:
        callback: The unpacked button data.
        sources: The Sources config.

    Returns:
        The decision and the Vacancy, or ``None`` when the button's Source is
        no longer in the config.
    """
    for source in sources.sources:
        if source.id == callback.source_id:
            return callback.decision, VacancyKey(source.company, callback.vacancy_id)
    logger.warning("Button of unknown Source %r", callback.source_id)
    return None


def create_telegram_notifier(
    settings: Settings, sources: SourcesConfig
) -> TelegramNotifier | None:
    """Create the real notifier from the Telegram settings.

    Args:
        settings: Settings for this run.
        sources: The Sources config, used to name Companies in buttons.

    Returns:
        The notifier, or ``None`` when the bot token is not set, empty or
        whitespace-only, or the chat ID is not set.

    Raises:
        TelegramSetupError: If the bot token is malformed.
    """
    token = settings.telegram_bot_token
    if token is None or not token.get_secret_value().strip() or settings.telegram_chat_id is None:
        logger.info("Telegram is not configured, so the app has no notifier")
        return None
    try:
        bot = Bot(token.get_secret_value().strip())
    except TokenValidationError as error:
        logger.error("Malformed Telegram bot token: %s", error)
        raise TelegramSetupError("CV_JOB_MATCH_TELEGRAM_BOT_TOKEN is malformed") from error
    return TelegramNotifier(bot, settings.telegram_chat_id, sources)


def create_dispatcher(handlers: DecisionBot, sources: SourcesConfig) -> Dispatcher:
    """Create the dispatcher that routes ``/list`` and Vacancy buttons to the bot handlers.

    Args:
        handlers: The chat handlers, which ignore every chat but the configured one.
        sources: The Sources config, used to read the Company of a button.

    Returns:
        The dispatcher, ready to poll.
    """
    dispatcher = Dispatcher()

    @dispatcher.message(Command(LIST_COMMAND))
    async def answer_list_command(message: Message) -> None:
        """Answer ``/list`` in the configured chat."""
        reply = await handlers.handle_list_command(message.chat.id)
        if reply is not None:
            await message.answer(reply)

    @dispatcher.callback_query(VacancyCallback.filter())
    async def answer_vacancy_button(
        query: CallbackQuery, callback_data: VacancyCallback, bot: Bot
    ) -> None:
        """Apply a pressed Vacancy button and reply in the configured chat."""
        await query.answer()
        chat_id = query.message.chat.id if query.message else query.from_user.id
        parsed = parse_vacancy_callback(callback_data, sources)
        if parsed is None:
            reply = UNKNOWN_BUTTON_REPLY if handlers.is_configured_chat(chat_id) else None
        else:
            reply = await handlers.handle_button(chat_id, *parsed)
        if reply is not None:
            await bot.send_message(chat_id, reply)

    return dispatcher
