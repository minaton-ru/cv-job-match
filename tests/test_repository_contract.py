import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from cv_job_match.app import Repositories
from cv_job_match.db.postgres import (
    PostgresProfileRepository,
    PostgresRepository,
    migrate_database,
)
from cv_job_match.db.schema import metadata
from cv_job_match.decisions import AssessedVacancy
from cv_job_match.discovery import KnownVacancy, NewVacancy, Revision, RunRecord, RunStatus
from cv_job_match.evaluation import (
    Assessment,
    AssessmentResult,
    PrefilterRejection,
    VacancyKey,
    WaitingVacancy,
)
from cv_job_match.exceptions import VacancyNotFoundError
from cv_job_match.profile import load_profile
from cv_job_match.tailoring import ApplicationPack, ShortlistedVacancy
from tests.fakes import FakeDiscoveryRepository, FakeProfileRepository

TEST_DATABASE_URL_VARIABLE = "CV_JOB_MATCH_TEST_DATABASE_URL"
DROP_ALL_TABLES = ("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
START = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
ALPHA = "Alpha Corp"
BETA = "Beta Corp"


async def reset_database(database_url: str, *statements: str) -> None:
    """Run the statements that reset the test database."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    await engine.dispose()


def find_schema_differences(connection: Connection) -> list[object]:
    """List how the migrated schema differs from the models."""
    return compare_metadata(MigrationContext.configure(connection), metadata)


async def compare_schema_with_models(database_url: str) -> list[object]:
    """List how the test database schema differs from the models."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        differences = await connection.run_sync(find_schema_differences)
    await engine.dispose()
    return differences


@pytest.fixture(scope="session")
def database_url() -> str:
    """Return the test database URL with a freshly migrated schema, or skip."""
    url = os.environ.get(TEST_DATABASE_URL_VARIABLE)
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_VARIABLE} is not set")
    asyncio.run(reset_database(url, *DROP_ALL_TABLES))
    migrate_database(url)
    return url


def build_repositories(repository: object, profile: object) -> Repositories:
    """Wire one store for every module, as the app does."""
    return Repositories(
        discovery=repository,
        profile=profile,
        evaluation=repository,
        decision=repository,
        tailoring=repository,
    )


@pytest.fixture(params=["fake", pytest.param("postgres", marks=pytest.mark.postgres)])
def empty_repositories(request: pytest.FixtureRequest) -> Repositories:
    """Return empty repositories of each implementation."""
    if request.param == "fake":
        return build_repositories(FakeDiscoveryRepository(), FakeProfileRepository())
    url = request.getfixturevalue("database_url")
    names = ", ".join(table.name for table in metadata.sorted_tables)
    asyncio.run(reset_database(url, f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    engine = create_async_engine(url, poolclass=NullPool)
    return build_repositories(PostgresRepository(engine), PostgresProfileRepository(engine))


async def test_save_snapshot_only_when_version_is_new(empty_repositories: Repositories) -> None:
    snapshot = load_profile(Path("profile"))

    assert await empty_repositories.profile.save_snapshot(snapshot) is True
    assert await empty_repositories.profile.save_snapshot(snapshot) is False


def build_new_vacancy(vacancy_id: str, is_baseline: bool = False) -> NewVacancy:
    """Build a Vacancy recorded for the first time."""
    return NewVacancy(
        vacancy_id, {"title": f"Title {vacancy_id}"}, f"About {vacancy_id}", is_baseline
    )


def build_assessment(vacancy_id: str, minutes: int = 0) -> Assessment:
    """Build an Assessment of an Alpha Corp Vacancy made some minutes after START."""
    result = AssessmentResult(score=80, verdict="apply", strengths=["Python"], gaps=["Go"])
    return Assessment(
        VacancyKey(ALPHA, vacancy_id), result, "version-1", START + timedelta(minutes=minutes)
    )


def build_run(
    source_id: str,
    company: str,
    listed_ids: set[str],
    minutes: int = 0,
    status: RunStatus = "ok",
    new_vacancies: tuple[NewVacancy, ...] = (),
    revisions: tuple[Revision, ...] = (),
    closed_ids: frozenset[str] = frozenset(),
    reopened_ids: frozenset[str] = frozenset(),
) -> RunRecord:
    """Build a Scrape Run that started some minutes after START."""
    return RunRecord(
        source_id=source_id,
        company=company,
        started_at=START + timedelta(minutes=minutes),
        status=status,
        listed_ids=frozenset(listed_ids),
        error="HTTP 500" if status == "suspect" else None,
        new_vacancies=new_vacancies,
        revisions=revisions,
        closed_ids=closed_ids,
        reopened_ids=reopened_ids,
    )


@pytest.mark.postgres
def test_migrations_create_schema_matching_models(database_url: str) -> None:
    asyncio.run(reset_database(database_url, *DROP_ALL_TABLES))

    migrate_database(database_url)

    assert asyncio.run(compare_schema_with_models(database_url)) == []


async def test_load_state_uses_latest_successful_run_of_each_source(
    empty_repositories: Repositories,
) -> None:
    discovery = empty_repositories.discovery
    baseline = (build_new_vacancy("v1", is_baseline=True),)
    await discovery.save_run(build_run("alpha-backend", ALPHA, {"v1"}, 0, "baseline", baseline))
    await discovery.save_run(
        build_run("alpha-data", ALPHA, {"v1", "v2"}, 1, new_vacancies=(build_new_vacancy("v2"),))
    )
    await discovery.save_run(build_run("alpha-data", ALPHA, {"v2"}, 2))
    await discovery.save_run(build_run("alpha-data", ALPHA, set(), 3, "suspect"))

    backend = await discovery.load_state("alpha-backend", ALPHA)
    data = await discovery.load_state("alpha-data", ALPHA)
    unseen = await discovery.load_state("alpha-new", ALPHA)

    assert backend.last_successful_run.status == "baseline"
    assert backend.last_successful_run.listed_ids == {"v1"}
    assert backend.listed_by_other_sources == {"v2"}
    assert data.last_successful_run.started_at == START + timedelta(minutes=2)
    assert data.listed_by_other_sources == {"v1"}
    assert unseen.last_successful_run is None
    assert await discovery.load_last_runs() == {
        "alpha-backend": START,
        "alpha-data": START + timedelta(minutes=3),
    }


async def test_save_run_with_unknown_vacancy_saves_nothing(
    empty_repositories: Repositories,
) -> None:
    discovery = empty_repositories.discovery
    record = build_run(
        "alpha-backend",
        ALPHA,
        {"v1"},
        new_vacancies=(build_new_vacancy("v1"),),
        revisions=(Revision("ghost", {"title": "Ghost"}),),
    )

    with pytest.raises(VacancyNotFoundError, match="ghost"):
        await discovery.save_run(record)

    state = await discovery.load_state("alpha-backend", ALPHA)
    assert state.last_successful_run is None
    assert state.known_vacancies == {}
    assert await discovery.load_last_runs() == {}


async def test_vacancy_identity_is_company_plus_site_id(empty_repositories: Repositories) -> None:
    discovery = empty_repositories.discovery
    first_listing = (build_new_vacancy("v1"),)
    await discovery.save_run(
        build_run("alpha-backend", ALPHA, {"v1"}, new_vacancies=first_listing)
    )
    await discovery.save_run(build_run("beta-jobs", BETA, {"v1"}, new_vacancies=first_listing))
    city = (Revision("v1", {"city": "Example City"}),)
    await discovery.save_run(build_run("alpha-data", ALPHA, {"v1"}, 1, revisions=city))

    alpha = await discovery.load_state("alpha-data", ALPHA)
    beta = await discovery.load_state("beta-jobs", BETA)
    waiting = await empty_repositories.evaluation.load_waiting_vacancies()

    assert alpha.known_vacancies == {
        "v1": KnownVacancy({"title": "Title v1", "city": "Example City"}, is_closed=False)
    }
    assert beta.known_vacancies == {"v1": KnownVacancy({"title": "Title v1"}, is_closed=False)}
    assert {vacancy.key for vacancy in waiting} == {
        VacancyKey(ALPHA, "v1"),
        VacancyKey(BETA, "v1"),
    }


async def test_waiting_vacancies_exclude_baseline_closed_evaluated_and_decided(
    empty_repositories: Repositories,
) -> None:
    discovery = empty_repositories.discovery
    found = tuple(build_new_vacancy(f"v{number}") for number in range(1, 7))
    all_ids = {f"v{number}" for number in range(1, 7)}
    baseline = (build_new_vacancy("b1", is_baseline=True),)
    await discovery.save_run(build_run("alpha-backend", ALPHA, {"b1"}, 0, "baseline", baseline))
    await discovery.save_run(build_run("alpha-backend", ALPHA, all_ids, 1, new_vacancies=found))
    closed = frozenset({"v5", "v6"})
    await discovery.save_run(
        build_run("alpha-backend", ALPHA, all_ids - closed, 2, closed_ids=closed)
    )
    reopened = frozenset({"v6"})
    await discovery.save_run(build_run("alpha-backend", ALPHA, {"v6"}, 3, reopened_ids=reopened))
    await empty_repositories.evaluation.save_assessment(build_assessment("v2"))
    rejection = PrefilterRejection(
        VacancyKey(ALPHA, "v3"), "title has no keyword", "version-1", START
    )
    await empty_repositories.evaluation.save_prefilter_rejection(rejection)
    await empty_repositories.decision.save_status(VacancyKey(ALPHA, "v4"), "dismissed")

    waiting = await empty_repositories.evaluation.load_waiting_vacancies()

    assert waiting == [
        WaitingVacancy(VacancyKey(ALPHA, "v1"), {"title": "Title v1"}, "About v1"),
        WaitingVacancy(VacancyKey(ALPHA, "v6"), {"title": "Title v6"}, "About v6"),
    ]
    assert (
        await empty_repositories.evaluation.load_waiting_vacancy(VacancyKey(ALPHA, "v1"))
        == waiting[0]
    )
    assert (
        await empty_repositories.evaluation.load_waiting_vacancy(VacancyKey(ALPHA, "v2")) is None
    )


async def test_decisions_and_tailoring_read_back_saved_vacancy(
    empty_repositories: Repositories,
) -> None:
    found = (build_new_vacancy("v1"), build_new_vacancy("v2"))
    await empty_repositories.discovery.save_run(
        build_run("alpha-backend", ALPHA, {"v1", "v2"}, 0, "ok", found)
    )
    await empty_repositories.evaluation.save_assessment(build_assessment("v1", 0))
    await empty_repositories.evaluation.save_assessment(build_assessment("v2", 1))
    shortlisted_key = VacancyKey(ALPHA, "v1")
    await empty_repositories.decision.save_status(shortlisted_key, "shortlisted")
    await empty_repositories.tailoring.save_application_pack(
        ApplicationPack(shortlisted_key, "version-1", "en", START)
    )
    unknown_key = VacancyKey(ALPHA, "unknown")

    assert await empty_repositories.decision.load_recent_assessed(limit=1) == [
        AssessedVacancy({"title": "Title v2"}, build_assessment("v2", 1), "new")
    ]
    assert await empty_repositories.decision.load_status(shortlisted_key) == "shortlisted"
    assert await empty_repositories.decision.load_status(unknown_key) is None
    assert await empty_repositories.tailoring.load_shortlisted_vacancy(
        shortlisted_key
    ) == ShortlistedVacancy(shortlisted_key, "alpha-backend", {"title": "Title v1"}, "About v1")
    assert (
        await empty_repositories.tailoring.load_shortlisted_vacancy(VacancyKey(ALPHA, "v2"))
        is None
    )
    with pytest.raises(VacancyNotFoundError, match="unknown"):
        await empty_repositories.decision.save_status(unknown_key, "applied")
