from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cv_job_match.app import App, Repositories, build_app
from cv_job_match.exceptions import DependencyNotConfiguredError, HttpRequestError
from cv_job_match.http_client import HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import (
    CannedAnswer,
    FakeChatModel,
    FakeClock,
    FakeDiscoveryRepository,
    FakeHttpClient,
    FakeNotifier,
    build_assessment_message,
    build_fake_chat_model,
    build_json_response,
)
from tests.sources_files import write_sources_yaml

START = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
ALPHA_LIST_URL = "https://careers.alpha.example/api/jobs"
BETA_LIST_URL = "https://careers.beta.example/api/jobs"
GAMMA_LIST_URL = "https://careers.gamma.example/api/jobs"


def build_source_yaml(source_id: str, company: str, list_url: str) -> str:
    return (
        f"  - id: {source_id}\n"
        f"    company: {company}\n"
        "    technique: json_endpoint\n"
        f"    list: {{url: '{list_url}', items_path: items, id_field: id, "
        "fields: {title: name, city: town, description: text}}\n"
    )


def build_list_response(*vacancy_ids: str) -> HttpResponse:
    return build_json_response(
        {
            "items": [
                {"id": vacancy_id, "name": "Python Developer", "town": "Example City", "text": ""}
                for vacancy_id in vacancy_ids
            ]
        }
    )


def build_alert_app(
    tmp_path: Path,
    sources: str,
    responses: dict[str, CannedAnswer | list[CannedAnswer]],
    notifier: FakeNotifier | None,
    llm: FakeChatModel | None = None,
) -> tuple[App, FakeDiscoveryRepository, FakeClock]:
    repository = FakeDiscoveryRepository()
    clock = FakeClock(START)
    settings = Settings(
        sources_path=write_sources_yaml(tmp_path, "sources:\n" + sources), _env_file=None
    )
    app = build_app(
        settings,
        repos=Repositories(discovery=repository, evaluation=repository),
        llm=llm or build_fake_chat_model(),
        http=FakeHttpClient(responses),
        notifier=notifier,
        clock=clock,
    )
    return app, repository, clock


@pytest.mark.parametrize(
    ("second_list", "expected_alerts"),
    [
        (
            HttpRequestError("connection refused"),
            [
                "Suspect Scrape Run of Source alpha-backend (Alpha Corp): "
                "request failed: connection refused"
            ],
        ),
        (
            build_list_response(),
            [
                "Suspect Scrape Run of Source alpha-backend (Alpha Corp): "
                "found 0 Vacancies, the last successful run found 2"
            ],
        ),
        (build_list_response("1", "2"), []),
    ],
    ids=["list-fetch-failed", "empty-after-nonempty-success", "normal-run"],
)
async def test_run_tick_alerts_once_per_suspect_run(
    tmp_path: Path, second_list: CannedAnswer, expected_alerts: list[str]
) -> None:
    notifier = FakeNotifier()
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: [build_list_response("1", "2"), second_list]
    }
    app, _, clock = build_alert_app(
        tmp_path,
        build_source_yaml("alpha-backend", "Alpha Corp", ALPHA_LIST_URL),
        responses,
        notifier,
    )
    await app.run_tick()
    clock.now += timedelta(days=1)

    await app.run_tick()

    assert notifier.messages == expected_alerts


async def test_run_tick_failed_alert_does_not_stop_other_alerts_or_evaluation(
    tmp_path: Path,
) -> None:
    notifier = FakeNotifier(fail_on="alpha-backend")
    llm = build_fake_chat_model(build_assessment_message(score=90, verdict="apply"))
    sources = (
        build_source_yaml("alpha-backend", "Alpha Corp", ALPHA_LIST_URL)
        + build_source_yaml("beta-data", "Beta Inc", BETA_LIST_URL)
        + build_source_yaml("gamma-ops", "Gamma Ltd", GAMMA_LIST_URL)
    )
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: [build_list_response("1"), HttpRequestError("connection refused")],
        BETA_LIST_URL: [build_list_response(), build_list_response("b1")],
        GAMMA_LIST_URL: [build_list_response("1"), build_list_response()],
    }
    app, _, clock = build_alert_app(tmp_path, sources, responses, notifier, llm)
    await app.run_tick()
    clock.now += timedelta(days=1)

    report = await app.run_tick()

    assert report.alert_count == 1
    assert [assessment.key.vacancy_id for assessment in report.assessments] == ["b1"]
    assert notifier.messages[0] == (
        "Suspect Scrape Run of Source gamma-ops (Gamma Ltd): "
        "found 0 Vacancies, the last successful run found 1"
    )
    assert notifier.messages[1].startswith("Beta Inc: Python Developer")
    assert len(notifier.messages) == 2


async def test_run_tick_without_notifier_raises_before_discovery(tmp_path: Path) -> None:
    app, repository, _ = build_alert_app(
        tmp_path,
        build_source_yaml("alpha-backend", "Alpha Corp", ALPHA_LIST_URL),
        {ALPHA_LIST_URL: build_list_response("1")},
        None,
    )

    with pytest.raises(DependencyNotConfiguredError, match="needs a notifier"):
        await app.run_tick()

    assert repository.runs == []
