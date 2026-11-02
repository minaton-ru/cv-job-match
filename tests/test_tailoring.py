import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cv_job_match.app import App, Repositories, build_app
from cv_job_match.discovery import NewVacancy
from cv_job_match.evaluation import VacancyKey
from cv_job_match.exceptions import HttpRequestError
from cv_job_match.settings import Settings
from cv_job_match.tailoring import ApplicationPack
from tests.fakes import (
    CannedAnswer,
    FakeChatModel,
    FakeClock,
    FakeDiscoveryRepository,
    FakeHttpClient,
    FakeNotifier,
    FakeStoredVacancy,
    build_fake_chat_model,
    build_json_response,
    build_tailoring_message,
)
from tests.sources_files import write_sources_yaml

START = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
CHAT_ID = 1001
LIST_URL = "https://careers.alpha.example/api/jobs"
DETAIL_URL = "https://careers.alpha.example/api/jobs/v1"
SOURCES_YAML = f"""
sources:
  - id: alpha-backend
    company: Alpha Corp
    technique: json_endpoint
    list:
      url: '{LIST_URL}'
      items_path: items
      id_field: id
      fields: {{title: name}}
    detail:
      url: '{LIST_URL}/{{id}}'
      description_fields: [summary]
"""
VACANCY_KEY = VacancyKey("Alpha Corp", "v1")
RESUME = "# Sam Example\n\nPython developer."
COVER_LETTER = "Dear hiring team, I would like to apply."
DESCRIPTION = build_json_response({"summary": "Build Python services."})


def build_tailoring_app(
    tmp_path: Path, details: list[CannedAnswer], llm: FakeChatModel
) -> tuple[App, FakeDiscoveryRepository, FakeHttpClient, FakeNotifier]:
    repository = FakeDiscoveryRepository()
    fields = {"title": "Python Developer"}
    repository.vacancies[("Alpha Corp", "v1")] = FakeStoredVacancy(
        NewVacancy("v1", fields, "Old text", False), dict(fields), source_id="alpha-backend"
    )
    http = FakeHttpClient({DETAIL_URL: details})
    notifier = FakeNotifier()
    settings = Settings(
        sources_path=write_sources_yaml(tmp_path, SOURCES_YAML),
        telegram_chat_id=CHAT_ID,
        _env_file=None,
    )
    app = build_app(
        settings,
        repos=Repositories(discovery=repository, decision=repository, tailoring=repository),
        llm=llm,
        http=http,
        notifier=notifier,
        clock=FakeClock(START),
    )
    return app, repository, http, notifier


async def press_shortlist(app: App) -> str | None:
    reply = await app.bot.handle_button(CHAT_ID, "shortlisted", VACANCY_KEY)
    await app.tailoring.wait_for_background_tasks()
    return reply


async def test_shortlist_sends_application_pack(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="cv_job_match")
    llm = build_fake_chat_model(
        build_tailoring_message(resume_markdown=RESUME, cover_letter=COVER_LETTER)
    )
    app, repository, http, notifier = build_tailoring_app(tmp_path, [DESCRIPTION], llm)

    reply = await press_shortlist(app)

    assert reply == "Vacancy v1 of Alpha Corp is now shortlisted."
    assert [request.url for request in http.requests] == [DETAIL_URL]
    assert [name for name, _ in notifier.documents] == ["resume.pdf", "resume.md"]
    assert notifier.documents[1][1] == RESUME.encode("utf-8")
    assert notifier.messages == [COVER_LETTER]
    assert repository.vacancies[("Alpha Corp", "v1")].application_pack == ApplicationPack(
        VACANCY_KEY, app.profile.snapshot.version, "en", START
    )
    assert "Sent Application Pack for Vacancy v1 of Alpha Corp" in caplog.text


@pytest.mark.parametrize(
    ("details", "answers", "reason"),
    [
        (
            [HttpRequestError("connection refused"), DESCRIPTION],
            [],
            "the description could not be fetched again",
        ),
        ([DESCRIPTION, DESCRIPTION], [RuntimeError("model is down")], "the AI call failed"),
    ],
)
async def test_failed_tailoring_sends_clear_message_and_shortlist_retries(
    tmp_path: Path, details: list[CannedAnswer], answers: list[Exception], reason: str
) -> None:
    pack_answer = build_tailoring_message(resume_markdown=RESUME, cover_letter=COVER_LETTER)
    llm = build_fake_chat_model(*answers, pack_answer)
    app, repository, _, notifier = build_tailoring_app(tmp_path, details, llm)
    failure = (
        f"Tailoring of Vacancy v1 of Alpha Corp failed: {reason}. Press Shortlist to try again."
    )

    await press_shortlist(app)

    stored = repository.vacancies[("Alpha Corp", "v1")]
    assert notifier.messages == [failure]
    assert notifier.documents == []
    assert stored.application_pack is None

    await press_shortlist(app)

    assert notifier.messages == [failure, COVER_LETTER]
    assert len(notifier.documents) == 2
    assert stored.application_pack is not None
