import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cv_job_match.app import App, Repositories, build_app
from cv_job_match.discovery import RunRecord
from cv_job_match.exceptions import HttpRequestError, RepositoryNotConfiguredError
from cv_job_match.http_client import HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import (
    CannedAnswer,
    FakeClock,
    FakeDiscoveryRepository,
    FakeHttpClient,
    build_json_response,
)
from tests.sources_files import write_sources_yaml

START = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
ALPHA_LIST_URL = "https://careers.alpha.example/api/jobs"
ALPHA_DETAIL_URL = "https://careers.alpha.example/api/job"
BETA_LIST_URL = "https://careers.beta.example/api/jobs"


def build_source_yaml(
    source_id: str, company: str, list_url: str, extra: str = "", detail_url: str = ""
) -> str:
    text = (
        f"  - id: {source_id}\n"
        f"    company: {company}\n"
        "    technique: json_endpoint\n"
        f"    list: {{url: '{list_url}', items_path: items, id_field: id, "
        "fields: {title: name, description: text}}\n"
    )
    if detail_url:
        text += f"    detail: {{url: '{detail_url}/{{id}}', description_fields: [body]}}\n"
    return text + extra


ALPHA_SOURCE = build_source_yaml(
    "alpha-backend", "Alpha Corp", ALPHA_LIST_URL, detail_url=ALPHA_DETAIL_URL
)


def build_list_response(*vacancy_ids: str) -> HttpResponse:
    return build_json_response(
        {"items": [{"id": vacancy_id, "name": f"Role {vacancy_id}"} for vacancy_id in vacancy_ids]}
    )


def build_detail_response(vacancy_id: str) -> HttpResponse:
    return build_json_response({"body": f"Text {vacancy_id}"})


def build_discovery_app(
    tmp_path: Path,
    sources: str,
    responses: dict[str, CannedAnswer | list[CannedAnswer]],
    repository: FakeDiscoveryRepository | None = None,
) -> tuple[App, FakeDiscoveryRepository, FakeClock]:
    repository = repository or FakeDiscoveryRepository()
    clock = FakeClock(START)
    settings = Settings(
        sources_path=write_sources_yaml(tmp_path, "sources:\n" + sources), _env_file=None
    )
    app = build_app(
        settings,
        repos=Repositories(discovery=repository),
        http=FakeHttpClient(responses),
        clock=clock,
    )
    return app, repository, clock


def list_run_sources(runs: Sequence[RunRecord]) -> list[str]:
    return [run.source_id for run in runs]


async def test_run_due_checks_never_run_enabled_sources_only(tmp_path: Path) -> None:
    disabled = build_source_yaml("beta-data", "Beta Inc", BETA_LIST_URL, "    enabled: false\n")
    app, repository, _ = build_discovery_app(
        tmp_path, ALPHA_SOURCE + disabled, {ALPHA_LIST_URL: build_list_response("1")}
    )

    report = await app.discovery.run_due()

    assert list_run_sources(report.runs) == ["alpha-backend"]
    assert list_run_sources(repository.runs) == ["alpha-backend"]


async def test_run_due_skips_source_until_interval_has_passed(tmp_path: Path) -> None:
    app, _, clock = build_discovery_app(
        tmp_path, ALPHA_SOURCE, {ALPHA_LIST_URL: build_list_response("1")}
    )
    await app.discovery.run_due()

    clock.now = START + timedelta(hours=23, minutes=59)
    early = await app.discovery.run_due()
    clock.now = START + timedelta(days=1)
    on_time = await app.discovery.run_due()

    assert early.runs == ()
    assert list_run_sources(on_time.runs) == ["alpha-backend"]


async def test_run_due_respects_source_interval_override(tmp_path: Path) -> None:
    beta = build_source_yaml("beta-data", "Beta Inc", BETA_LIST_URL, "    interval: 12h\n")
    responses = {ALPHA_LIST_URL: build_list_response("1"), BETA_LIST_URL: build_list_response()}
    app, _, clock = build_discovery_app(tmp_path, ALPHA_SOURCE + beta, responses)
    await app.discovery.run_due()

    clock.now = START + timedelta(hours=12)
    report = await app.discovery.run_due()

    assert list_run_sources(report.runs) == ["beta-data"]


async def test_run_due_waits_full_interval_after_failed_run(tmp_path: Path) -> None:
    app, repository, clock = build_discovery_app(
        tmp_path, ALPHA_SOURCE, {ALPHA_LIST_URL: HttpRequestError("connection refused")}
    )
    await app.discovery.run_due()

    clock.now = START + timedelta(hours=1)
    report = await app.discovery.run_due()

    assert report.runs == ()
    assert [run.status for run in repository.runs] == ["suspect"]


def list_vacancies(repository: FakeDiscoveryRepository) -> dict[str, tuple[str | None, bool]]:
    return {
        vacancy_id: (vacancy.description, vacancy.is_baseline)
        for (_, vacancy_id), vacancy in repository.vacancies.items()
    }


async def test_run_due_records_first_run_as_baseline(tmp_path: Path) -> None:
    app, repository, _ = build_discovery_app(
        tmp_path, ALPHA_SOURCE, {ALPHA_LIST_URL: build_list_response("1", "2")}
    )

    report = await app.discovery.run_due()

    assert [(run.status, run.found_count) for run in report.runs] == [("baseline", 2)]
    assert list_vacancies(repository) == {"1": (None, True), "2": (None, True)}


async def test_run_due_makes_first_successful_run_the_baseline_after_network_failure(
    tmp_path: Path,
) -> None:
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: [HttpRequestError("connection refused"), build_list_response("1")]
    }
    app, repository, clock = build_discovery_app(tmp_path, ALPHA_SOURCE, responses)

    failed = await app.discovery.run_due()
    clock.now = START + timedelta(days=1)
    await app.discovery.run_due()

    assert failed.runs[0].status == "suspect"
    assert "connection refused" in failed.runs[0].error
    assert [run.status for run in repository.runs] == ["suspect", "baseline"]
    assert list_vacancies(repository) == {"1": (None, True)}


async def test_run_due_records_vacancy_found_after_baseline_as_new(tmp_path: Path) -> None:
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: [build_list_response("1"), build_list_response("1", "2")],
        f"{ALPHA_DETAIL_URL}/2": build_detail_response("2"),
    }
    app, repository, clock = build_discovery_app(tmp_path, ALPHA_SOURCE, responses)
    await app.discovery.run_due()

    clock.now = START + timedelta(days=1)
    report = await app.discovery.run_due()

    assert report.runs[0].status == "ok"
    assert [vacancy.vacancy_id for vacancy in report.runs[0].new_vacancies] == ["2"]
    assert list_vacancies(repository) == {"1": (None, True), "2": ("Text 2", False)}


async def test_run_due_retries_vacancy_whose_description_failed(tmp_path: Path) -> None:
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: [
            build_list_response(),
            build_list_response("3"),
            build_list_response("3"),
        ],
        f"{ALPHA_DETAIL_URL}/3": [
            build_json_response({}, status_code=503),
            build_detail_response("3"),
        ],
    }
    app, repository, clock = build_discovery_app(tmp_path, ALPHA_SOURCE, responses)
    await app.discovery.run_due()

    clock.now = START + timedelta(days=1)
    failed = await app.discovery.run_due()
    clock.now = START + timedelta(days=2)
    retried = await app.discovery.run_due()

    assert (failed.runs[0].found_count, failed.runs[0].new_vacancies) == (1, ())
    assert [vacancy.vacancy_id for vacancy in retried.runs[0].new_vacancies] == ["3"]
    assert list_vacancies(repository) == {"3": ("Text 3", False)}


async def test_run_due_records_duplicate_within_list_once(tmp_path: Path) -> None:
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: [build_list_response(), build_list_response("4", "4")],
        f"{ALPHA_DETAIL_URL}/4": build_detail_response("4"),
    }
    app, _, clock = build_discovery_app(tmp_path, ALPHA_SOURCE, responses)
    await app.discovery.run_due()

    clock.now = START + timedelta(days=1)
    report = await app.discovery.run_due()

    assert report.runs[0].found_count == 1
    assert [vacancy.vacancy_id for vacancy in report.runs[0].new_vacancies] == ["4"]


async def test_run_due_keeps_running_other_sources_after_network_failure(tmp_path: Path) -> None:
    beta = build_source_yaml("beta-data", "Beta Inc", BETA_LIST_URL)
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: HttpRequestError("connection refused"),
        BETA_LIST_URL: build_list_response("1"),
    }
    app, _, _ = build_discovery_app(tmp_path, ALPHA_SOURCE + beta, responses)

    report = await app.discovery.run_due()

    assert [(run.source_id, run.status) for run in report.runs] == [
        ("alpha-backend", "suspect"),
        ("beta-data", "baseline"),
    ]


async def test_run_due_records_invalid_technique_options_as_suspect(tmp_path: Path) -> None:
    broken = (
        "  - {id: gamma-ops, company: Gamma, technique: json_endpoint, "
        f"list: {{url: '{BETA_LIST_URL}', items_path: items}}}}\n"
    )
    app, repository, _ = build_discovery_app(tmp_path, broken, {})

    report = await app.discovery.run_due()

    assert report.runs[0].status == "suspect"
    assert "id_field" in report.runs[0].error
    assert repository.runs == list(report.runs)


async def test_run_due_propagates_repository_error_and_saves_nothing(tmp_path: Path) -> None:
    repository = FakeDiscoveryRepository()
    repository.save_error = RuntimeError("database is down")
    app, _, _ = build_discovery_app(
        tmp_path, ALPHA_SOURCE, {ALPHA_LIST_URL: build_list_response("1")}, repository
    )

    with pytest.raises(RuntimeError, match="database is down"):
        await app.discovery.run_due()

    assert repository.runs == []
    assert repository.vacancies == {}


async def test_run_due_runs_sources_of_one_company_in_order(tmp_path: Path) -> None:
    second = build_source_yaml("alpha-data", "Alpha Corp", BETA_LIST_URL)
    responses = {ALPHA_LIST_URL: build_list_response("1"), BETA_LIST_URL: build_list_response("1")}
    app, repository, _ = build_discovery_app(tmp_path, ALPHA_SOURCE + second, responses)

    await app.discovery.run_due()

    assert list_run_sources(repository.runs) == ["alpha-backend", "alpha-data"]
    assert [len(run.new_vacancies) for run in repository.runs] == [1, 0]


async def test_run_due_logs_each_scrape_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    beta = build_source_yaml("beta-data", "Beta Inc", BETA_LIST_URL)
    responses: dict[str, CannedAnswer | list[CannedAnswer]] = {
        ALPHA_LIST_URL: build_list_response("1", "2"),
        BETA_LIST_URL: HttpRequestError("connection refused"),
    }
    app, _, _ = build_discovery_app(tmp_path, ALPHA_SOURCE + beta, responses)
    caplog.set_level(logging.INFO, logger="cv_job_match")

    await app.discovery.run_due()

    assert "Scrape Run of Source alpha-backend: baseline, found 2, new 2" in caplog.text
    assert "Scrape Run of Source beta-data: suspect, found 0, new 0" in caplog.text
    assert "connection refused" in caplog.text


async def test_run_due_without_repository_raises(tmp_path: Path) -> None:
    settings = Settings(
        sources_path=write_sources_yaml(tmp_path, "sources:\n" + ALPHA_SOURCE), _env_file=None
    )
    app = build_app(settings, http=FakeHttpClient({}))

    with pytest.raises(RepositoryNotConfiguredError, match="needs a repository"):
        await app.discovery.run_due()
