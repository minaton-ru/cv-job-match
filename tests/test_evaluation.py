import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from cv_job_match.app import App, Repositories, build_app
from cv_job_match.evaluation import VacancyKey
from cv_job_match.exceptions import DependencyNotConfiguredError, RepositoryNotConfiguredError
from cv_job_match.http_client import HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import (
    FakeChatModel,
    FakeClock,
    FakeDiscoveryRepository,
    FakeHttpClient,
    FakeNotifier,
    build_assessment_message,
    build_fake_chat_model,
    build_json_response,
)
from tests.profile_files import CRITERIA_YAML, write_profile
from tests.sources_files import write_sources_yaml

START = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
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


def build_list_response(
    *vacancy_ids: str, title: str = "Python Developer", city: str = "Example City"
) -> HttpResponse:
    return build_json_response(
        {
            "items": [
                {
                    "id": vacancy_id,
                    "name": f"{title} {vacancy_id}",
                    "link": f"https://careers.alpha.example/jobs/{vacancy_id}",
                    "town": city,
                    "text": f"Text {vacancy_id}",
                }
                for vacancy_id in vacancy_ids
            ]
        }
    )


def build_evaluation_app(
    tmp_path: Path,
    llm: FakeChatModel | None,
    lists: list[HttpResponse],
    notifier: FakeNotifier | None = None,
    repos: Repositories | None = None,
) -> tuple[App, FakeDiscoveryRepository, FakeClock]:
    repository = FakeDiscoveryRepository()
    clock = FakeClock(START)
    settings = Settings(sources_path=write_sources_yaml(tmp_path, SOURCES_YAML), _env_file=None)
    app = build_app(
        settings,
        repos=repos or Repositories(discovery=repository, evaluation=repository),
        llm=llm,
        http=FakeHttpClient({LIST_URL: list(lists)}),
        notifier=notifier,
        clock=clock,
    )
    return app, repository, clock


async def discover_new_vacancy(app: App, clock: FakeClock) -> None:
    await app.discovery.run_due()
    clock.now += timedelta(days=1)
    await app.discovery.run_due()


@pytest.mark.parametrize(("score", "is_sent"), [(74, False), (75, True)])
async def test_run_tick_assesses_new_vacancy_and_notifies_from_threshold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, score: int, is_sent: bool
) -> None:
    answer = build_assessment_message(
        score=score, verdict="apply", strengths=["Python"], gaps=["Go"], exclusions=[]
    )
    write_profile(tmp_path / "profile", criteria=CRITERIA_YAML + "score_threshold: 75\n")
    notifier = FakeNotifier()
    app, repository, clock = build_evaluation_app(
        tmp_path,
        build_fake_chat_model(answer),
        [build_list_response(), build_list_response("v1")],
        notifier,
    )
    caplog.set_level(logging.INFO, logger="cv_job_match")
    await app.run_tick()
    clock.now += timedelta(days=1)

    report = await app.run_tick()

    [assessment] = report.assessments
    assert assessment.key == VACANCY_KEY
    assert assessment.result.score == score
    assert assessment.profile_version == app.profile.snapshot.version
    assert repository.vacancies[("Alpha Corp", "v1")].assessment == assessment
    expected = (
        f"Alpha Corp: Python Developer v1\nhttps://careers.alpha.example/jobs/v1\n"
        f"Score {score}, verdict: apply\nStrengths: Python\nGaps: Go\nExclusions: none"
    )
    assert notifier.messages == ([expected] if is_sent else [])
    assert f"Assessed Vacancy v1 of Alpha Corp: score {score}, verdict apply" in caplog.text


async def test_run_tick_never_assesses_baseline_vacancies(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    app, repository, _ = build_evaluation_app(
        tmp_path, build_fake_chat_model(), [build_list_response("v1")], notifier
    )

    report = await app.run_tick()

    assert report.assessments == ()
    assert repository.vacancies[("Alpha Corp", "v1")].assessment is None
    assert await app.evaluation.evaluate_vacancy(VACANCY_KEY) is None
    assert notifier.messages == []


async def test_run_tick_assesses_vacancy_saved_before_crash(tmp_path: Path) -> None:
    llm = build_fake_chat_model(build_assessment_message(score=80, verdict="apply"))
    app, _, clock = build_evaluation_app(
        tmp_path, llm, [build_list_response(), build_list_response("v1")], FakeNotifier()
    )
    await discover_new_vacancy(app, clock)

    report = await app.run_tick()

    assert report.discovery.runs == ()
    assert [assessment.key for assessment in report.assessments] == [VACANCY_KEY]


@pytest.mark.parametrize(
    "failed_answer",
    [
        RuntimeError("provider is down"),
        build_assessment_message(score=500, verdict="apply"),
        AIMessage(content="no tool call"),
    ],
)
async def test_run_tick_keeps_vacancy_waiting_after_failed_assessment_and_retries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, failed_answer: AIMessage | Exception
) -> None:
    llm = build_fake_chat_model(failed_answer, build_assessment_message(score=70, verdict="maybe"))
    notifier = FakeNotifier()
    app, repository, clock = build_evaluation_app(
        tmp_path, llm, [build_list_response(), build_list_response("v1")], notifier
    )
    await discover_new_vacancy(app, clock)
    caplog.set_level(logging.INFO, logger="cv_job_match")

    failed = await app.run_tick()

    assert failed.assessments == ()
    assert repository.vacancies[("Alpha Corp", "v1")].assessment is None
    assert notifier.messages == []
    assert "Assessment of Vacancy v1 of Alpha Corp failed, it keeps waiting" in caplog.text

    retried = await app.run_tick()

    assert [assessment.result.score for assessment in retried.assessments] == [70]
    assert len(notifier.messages) == 1


@pytest.mark.parametrize(
    ("title", "city", "reason"),
    [
        ("Java Developer", "Example City", "title has none of 'python', 'backend'"),
        ("Python Developer", "Other Town", "city 'Other Town' is not one of 'Example City'"),
    ],
)
async def test_run_tick_filters_out_mismatch_without_ai_call(
    tmp_path: Path, title: str, city: str, reason: str
) -> None:
    llm = build_fake_chat_model(build_assessment_message(score=90, verdict="apply"))
    notifier = FakeNotifier()
    new_list = build_list_response("v1", title=title, city=city)
    app, repository, clock = build_evaluation_app(
        tmp_path, llm, [build_list_response(), new_list], notifier
    )
    await discover_new_vacancy(app, clock)

    report = await app.run_tick()

    stored = repository.vacancies[("Alpha Corp", "v1")]
    assert report.assessments == ()
    assert stored.assessment is None
    assert stored.rejection is not None
    assert stored.rejection.reason == reason
    assert stored.rejection.profile_version == app.profile.snapshot.version
    assert notifier.messages == []
    assert next(llm.messages, None) is not None, "the AI answer must stay unrequested"
    assert await app.evaluation.evaluate_vacancy(VACANCY_KEY) is None


def test_build_app_creates_chat_model_from_settings(tmp_path: Path) -> None:
    settings = Settings(
        sources_path=write_sources_yaml(tmp_path, SOURCES_YAML),
        llm_base_url="https://llm.example/v1",
        llm_model="example-flash",
        llm_api_key="test-key",
        _env_file=None,
    )

    app = build_app(settings)

    assert isinstance(app.llm, ChatOpenAI)
    assert app.llm.model_name == "example-flash"
    assert app.llm.openai_api_base == "https://llm.example/v1"
    assert build_app(settings.model_copy(update={"llm_api_key": None})).llm is None


@pytest.mark.parametrize(
    ("missing", "error", "match"),
    [
        ("repository", RepositoryNotConfiguredError, "needs a repository"),
        ("llm", DependencyNotConfiguredError, "needs a chat model"),
        ("notifier", DependencyNotConfiguredError, "needs a notifier"),
    ],
)
async def test_evaluate_waiting_without_dependency_raises(
    tmp_path: Path, missing: str, error: type[Exception], match: str
) -> None:
    repos = Repositories(discovery=FakeDiscoveryRepository()) if missing == "repository" else None
    app, _, _ = build_evaluation_app(
        tmp_path,
        None if missing == "llm" else build_fake_chat_model(),
        [],
        None if missing == "notifier" else FakeNotifier(),
        repos,
    )

    with pytest.raises(error, match=match):
        await app.evaluation.evaluate_waiting()
