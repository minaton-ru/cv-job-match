from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cv_job_match.app import App, Repositories, build_app
from cv_job_match.decisions import Decision, VacancyStatus
from cv_job_match.discovery import NewVacancy
from cv_job_match.evaluation import Assessment, AssessmentResult, VacancyKey
from cv_job_match.http_client import HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import (
    FakeChatModel,
    FakeClock,
    FakeDiscoveryRepository,
    FakeHttpClient,
    FakeNotifier,
    FakeStoredVacancy,
    build_assessment_message,
    build_fake_chat_model,
    build_json_response,
)
from tests.sources_files import write_sources_yaml

START = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
CHAT_ID = 1001
LIST_URL = "https://careers.alpha.example/api/jobs"
SOURCES_YAML = f"""
sources:
  - id: alpha-backend
    company: Alpha Corp
    technique: json_endpoint
    list:
      url: '{LIST_URL}'
      items_path: items
      id_field: id
      fields: {{title: name, url: link, city: town, description: text}}
"""
VACANCY_KEY = VacancyKey("Alpha Corp", "v1")


def build_decision_app(
    tmp_path: Path,
    clock: FakeClock | None = None,
    lists: list[HttpResponse] | None = None,
    llm: FakeChatModel | None = None,
    notifier: FakeNotifier | None = None,
) -> tuple[App, FakeDiscoveryRepository]:
    repository = FakeDiscoveryRepository()
    settings = Settings(
        sources_path=write_sources_yaml(tmp_path, SOURCES_YAML),
        telegram_chat_id=CHAT_ID,
        _env_file=None,
    )
    app = build_app(
        settings,
        repos=Repositories(discovery=repository, evaluation=repository, decision=repository),
        llm=llm,
        http=FakeHttpClient({LIST_URL: lists or []}),
        notifier=notifier,
        clock=clock or FakeClock(START),
    )
    return app, repository


def build_list_response(*vacancy_ids: str, title: str = "Python Developer") -> HttpResponse:
    items = [
        {"id": vacancy_id, "name": f"{title} {vacancy_id}", "town": "Example City", "text": "Text"}
        for vacancy_id in vacancy_ids
    ]
    return build_json_response({"items": items})


def seed_vacancy(
    repository: FakeDiscoveryRepository,
    vacancy_id: str = "v1",
    status: VacancyStatus = "new",
    score: int | None = None,
    assessed_at: datetime = START,
) -> None:
    fields = {"title": f"Python Developer {vacancy_id}", "url": None}
    stored = FakeStoredVacancy(NewVacancy(vacancy_id, fields, None, False), dict(fields))
    stored.status = status
    if score is not None:
        result = AssessmentResult(score=score, verdict="maybe")
        key = VacancyKey("Alpha Corp", vacancy_id)
        stored.assessment = Assessment(key, result, "profile-v1", assessed_at)
    repository.vacancies[("Alpha Corp", vacancy_id)] = stored


@pytest.mark.parametrize(
    ("status", "decision"),
    [
        ("new", "shortlisted"),
        ("new", "applied"),
        ("new", "dismissed"),
        ("shortlisted", "applied"),
        ("shortlisted", "dismissed"),
    ],
)
async def test_button_moves_vacancy_along_allowed_transition(
    tmp_path: Path, status: VacancyStatus, decision: Decision
) -> None:
    app, repository = build_decision_app(tmp_path)
    seed_vacancy(repository, status=status)

    reply = await app.bot.handle_button(CHAT_ID, decision, VACANCY_KEY)

    assert reply == f"Vacancy v1 of Alpha Corp is now {decision}."
    assert repository.vacancies[("Alpha Corp", "v1")].status == decision


@pytest.mark.parametrize(
    ("status", "decision"),
    [("applied", "shortlisted"), ("dismissed", "applied"), ("shortlisted", "shortlisted")],
)
async def test_button_refuses_disallowed_transition(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, status: VacancyStatus, decision: Decision
) -> None:
    app, repository = build_decision_app(tmp_path)
    seed_vacancy(repository, status=status)

    reply = await app.bot.handle_button(CHAT_ID, decision, VACANCY_KEY)

    expected = f"Vacancy v1 of Alpha Corp is {status} and cannot become {decision}."
    assert reply == expected
    assert expected in caplog.text
    assert repository.vacancies[("Alpha Corp", "v1")].status == status


async def test_button_for_missing_vacancy_replies_clearly(tmp_path: Path) -> None:
    app, _ = build_decision_app(tmp_path)

    reply = await app.bot.handle_button(CHAT_ID, "dismissed", VACANCY_KEY)

    assert reply == "Vacancy v1 of Alpha Corp is not known."


@pytest.mark.parametrize(
    ("is_seeded", "expected"),
    [
        (
            True,
            "Recent Assessed Vacancies:\n"
            "Alpha Corp: Python Developer v2, score 40, maybe, new\n"
            "Alpha Corp: Python Developer v1, score 80, maybe, shortlisted",
        ),
        (False, "No Assessed Vacancies yet."),
    ],
)
async def test_list_command_shows_assessed_vacancies_below_threshold(
    tmp_path: Path, is_seeded: bool, expected: str
) -> None:
    app, repository = build_decision_app(tmp_path)
    if is_seeded:
        seed_vacancy(repository, "v1", "shortlisted", score=80)
        seed_vacancy(repository, "v2", score=40, assessed_at=START + timedelta(hours=1))
        seed_vacancy(repository, "v3")

    reply = await app.bot.handle_list_command(CHAT_ID)

    assert reply == expected


@pytest.mark.parametrize("command", ["button", "list"])
async def test_updates_from_other_chat_are_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, command: str
) -> None:
    app, repository = build_decision_app(tmp_path)
    seed_vacancy(repository, score=80)
    other_chat_id = CHAT_ID + 1

    if command == "button":
        reply = await app.bot.handle_button(other_chat_id, "dismissed", VACANCY_KEY)
    else:
        reply = await app.bot.handle_list_command(other_chat_id)

    assert reply is None
    assert repository.vacancies[("Alpha Corp", "v1")].status == "new"
    assert f"Ignored an update from chat {other_chat_id}" in caplog.text


@pytest.mark.parametrize("decision", ["dismissed", "applied"])
async def test_decided_vacancy_sends_no_message_after_revision_or_reopening(
    tmp_path: Path, decision: Decision
) -> None:
    clock = FakeClock(START)
    lists = [
        build_list_response(),
        build_list_response("v1", "v2"),
        build_list_response("v1", "v2", title="Senior Python Developer"),
        build_list_response("v2", title="Senior Python Developer"),
        build_list_response("v1", "v2", title="Senior Python Developer"),
    ]
    answers = [build_assessment_message(score=90, verdict="apply") for _ in range(4)]
    notifier = FakeNotifier()
    app, repository = build_decision_app(
        tmp_path, clock, lists, build_fake_chat_model(*answers), notifier
    )
    for _ in range(2):
        await app.discovery.run_due()
        clock.now += timedelta(days=1)
    await app.bot.handle_button(CHAT_ID, decision, VACANCY_KEY)

    for _ in range(3):
        await app.run_tick()
        clock.now += timedelta(days=1)

    stored = repository.vacancies[("Alpha Corp", "v1")]
    assert stored.fields["title"] == "Senior Python Developer v1"
    assert stored.is_closed is False
    assert stored.status == decision
    assert stored.assessment is None
    assert notifier.messages == [
        "Alpha Corp: Senior Python Developer v2\nno link\nScore 90, verdict: apply\n"
        "Strengths: none\nGaps: none\nExclusions: none"
    ]
