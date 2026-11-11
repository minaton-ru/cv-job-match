from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from cv_job_match.bot.telegram import TelegramNotifier, VacancyCallback, parse_vacancy_callback
from cv_job_match.evaluation import VacancyKey, build_decision_buttons
from cv_job_match.sources import SourcesConfig
from tests.fakes import FakeTelegramBot

CHAT_ID = 42
SOURCES = SourcesConfig.model_validate(
    {
        "sources": [
            {"id": "alpha-backend", "company": "Alpha Corp", "technique": "json_endpoint"},
            {"id": "alpha-data", "company": "Alpha Corp", "technique": "html_css"},
        ]
    }
)


def build_notifier(bot: FakeTelegramBot) -> TelegramNotifier:
    return TelegramNotifier(bot, CHAT_ID, SOURCES)


async def test_telegram_notifier_sends_message_with_decision_buttons() -> None:
    bot = FakeTelegramBot()
    key = VacancyKey("Alpha Corp", "v1")

    await build_notifier(bot).send_message("New Vacancy", build_decision_buttons(key))

    call = bot.messages[0]
    markup = call["reply_markup"]
    assert isinstance(markup, InlineKeyboardMarkup)
    buttons = markup.inline_keyboard[0]
    assert (call["chat_id"], call["text"]) == (CHAT_ID, "New Vacancy")
    assert [button.text for button in buttons] == ["Shortlist", "Mark applied", "Dismiss"]
    parsed = [
        parse_vacancy_callback(VacancyCallback.unpack(button.callback_data), SOURCES)
        for button in buttons
    ]
    assert parsed == [("shortlisted", key), ("applied", key), ("dismissed", key)]


async def test_telegram_notifier_sends_document_as_file() -> None:
    bot = FakeTelegramBot()

    await build_notifier(bot).send_document("resume.pdf", b"%PDF")

    document = bot.documents[0]["document"]
    assert isinstance(document, BufferedInputFile)
    assert (document.filename, document.data) == ("resume.pdf", b"%PDF")


def test_parse_vacancy_callback_returns_none_for_unknown_source() -> None:
    callback = VacancyCallback(decision="dismissed", source_id="removed-source", vacancy_id="v1")

    assert parse_vacancy_callback(callback, SOURCES) is None
