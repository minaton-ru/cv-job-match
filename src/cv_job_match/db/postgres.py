"""PostgreSQL implementations of the repositories, and the database migrations."""

import logging
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from alembic import command
from alembic.config import Config
from sqlalchemy import ColumnElement, Select, Table, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from cv_job_match.constants import MIGRATION_TARGET, MIGRATIONS_FOLDER_NAME
from cv_job_match.db.schema import (
    application_pack_table,
    assessment_table,
    company_table,
    prefilter_rejection_table,
    profile_snapshot_table,
    revision_table,
    run_listing_table,
    scrape_run_table,
    source_table,
    vacancy_table,
)
from cv_job_match.decisions import AssessedVacancy, VacancyStatus
from cv_job_match.discovery import KnownVacancy, RunRecord, SourceState
from cv_job_match.evaluation import (
    Assessment,
    AssessmentResult,
    PrefilterRejection,
    VacancyKey,
    WaitingVacancy,
)
from cv_job_match.exceptions import DependencyNotConfiguredError, VacancyNotFoundError
from cv_job_match.profile import ProfileSnapshot
from cv_job_match.settings import Settings
from cv_job_match.tailoring import ApplicationPack, ShortlistedVacancy

logger = logging.getLogger(__name__)


def create_database_engine(database_url: str) -> AsyncEngine:
    """Create the async engine every PostgreSQL repository shares.

    Args:
        database_url: SQLAlchemy URL of the database, with the asyncpg driver.

    Returns:
        The engine. It connects only when first used.
    """
    return create_async_engine(database_url)


def read_database_url() -> str:
    """Read the database URL from the settings.

    Returns:
        The value of ``CV_JOB_MATCH_DATABASE_URL``.

    Raises:
        DependencyNotConfiguredError: If the URL is not set.
    """
    database_url = Settings().database_url
    if database_url is None:
        logger.error("CV_JOB_MATCH_DATABASE_URL is not set")
        raise DependencyNotConfiguredError("CV_JOB_MATCH_DATABASE_URL is not set")
    return database_url.get_secret_value()


def migrate_database(database_url: str) -> None:
    """Bring the database schema up to the latest migration.

    It runs its own event loop, so call it outside a running one.

    Args:
        database_url: SQLAlchemy URL of the database, with the asyncpg driver.
    """
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / MIGRATIONS_FOLDER_NAME))
    config.attributes["database_url"] = database_url
    logger.info("Migrating the database to %s", MIGRATION_TARGET)
    command.upgrade(config, MIGRATION_TARGET)


class PostgresProfileRepository:
    """Storage of Profile snapshots in PostgreSQL, keyed by version."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Create the repository.

        Args:
            engine: The shared database engine.
        """
        self.engine = engine

    async def save_snapshot(self, snapshot: ProfileSnapshot) -> bool:
        """Save a snapshot unless its version is already stored.

        Args:
            snapshot: The Profile version to save.

        Returns:
            Whether the snapshot was saved, that is whether its version is new.
        """
        statement = (
            insert(profile_snapshot_table)
            .values(
                version=snapshot.version,
                resume=snapshot.resume,
                criteria=snapshot.criteria.model_dump(mode="json"),
            )
            .on_conflict_do_nothing()
            .returning(profile_snapshot_table.c.version)
        )
        async with self.engine.begin() as connection:
            saved = (await connection.execute(statement)).first() is not None
        logger.debug("Profile snapshot %s saved: %s", snapshot.version, saved)
        return saved


class PostgresRepository:
    """Storage in PostgreSQL for Discovery, Evaluation, Decisions and Tailoring."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Create the repository.

        Args:
            engine: The shared database engine.
        """
        self.engine = engine

    async def load_state(self, source_id: str, company: str) -> SourceState:
        """Load what is known before a Source is checked.

        Args:
            source_id: The Source to check.
            company: The Source's Company.

        Returns:
            The Source's last successful run, the Company's known Vacancies
            and the IDs listed by the latest successful run of its other Sources.
        """
        async with self.engine.connect() as connection:
            latest_runs = await load_latest_successful_runs(connection, company)
            known = await load_known_vacancies(connection, company)
        listed_by_others = frozenset(
            vacancy_id
            for other_id, run in latest_runs.items()
            if other_id != source_id
            for vacancy_id in run.listed_ids
        )
        logger.debug(
            "Loaded state of Source %s: %d known Vacancies, %d listed by other Sources",
            source_id,
            len(known),
            len(listed_by_others),
        )
        return SourceState(source_id, company, latest_runs.get(source_id), known, listed_by_others)

    async def load_last_runs(self) -> dict[str, datetime]:
        """Load when each Source last ran.

        Returns:
            Start time of each Source's latest Scrape Run of any status, by Source id.
        """
        statement = select(
            scrape_run_table.c.source_id, func.max(scrape_run_table.c.started_at)
        ).group_by(scrape_run_table.c.source_id)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).all()
        return {source_id: started_at for source_id, started_at in rows}

    async def save_run(self, record: RunRecord) -> None:
        """Save a Scrape Run together with all its changes, or nothing at all.

        The Company and the Source are mirrored from the config by name and ID.

        Args:
            record: The run to save.

        Raises:
            VacancyNotFoundError: If a Revision, closing or reopening names a
                Vacancy the Company does not have; then nothing is saved.
        """
        async with self.engine.begin() as connection:
            company_id = await save_company_source(connection, record.company, record.source_id)
            run_id = await insert_scrape_run(connection, record)
            if record.new_vacancies:
                await connection.execute(
                    insert(vacancy_table),
                    [
                        {
                            "company_id": company_id,
                            "site_id": vacancy.vacancy_id,
                            "source_id": record.source_id,
                            "fields": dict(vacancy.fields),
                            "description": vacancy.description,
                            "is_baseline": vacancy.is_baseline,
                        }
                        for vacancy in record.new_vacancies
                    ],
                )
            for revision in record.revisions:
                merged_fields = vacancy_table.c.fields.op("||", return_type=JSONB)(
                    literal(dict(revision.fields), JSONB)
                )
                updated = await update_company_vacancies(
                    connection,
                    record.company,
                    company_id,
                    {revision.vacancy_id},
                    fields=merged_fields,
                )
                await connection.execute(
                    insert(revision_table).values(
                        vacancy_id=updated[0], run_id=run_id, fields=dict(revision.fields)
                    )
                )
            for site_ids, is_closed in ((record.closed_ids, True), (record.reopened_ids, False)):
                if site_ids:
                    await update_company_vacancies(
                        connection, record.company, company_id, site_ids, is_closed=is_closed
                    )
        logger.info("Saved Scrape Run of Source %s as run %d", record.source_id, run_id)

    async def load_waiting_vacancies(self) -> list[WaitingVacancy]:
        """Load every Vacancy waiting to be assessed.

        Returns:
            Saved Vacancies that are not Baseline or Closed Vacancies, have no
            Assessment, were not filtered out and have no decision by the
            Candidate, in the order they were recorded.
        """
        async with self.engine.connect() as connection:
            rows = (await connection.execute(select_waiting_vacancies())).all()
        logger.debug("Loaded %d waiting Vacancies", len(rows))
        return [
            WaitingVacancy(VacancyKey(row.name, row.site_id), row.fields, row.description)
            for row in rows
        ]

    async def load_waiting_vacancy(self, key: VacancyKey) -> WaitingVacancy | None:
        """Load one Vacancy if it is waiting to be assessed.

        Args:
            key: Identity of the Vacancy.

        Returns:
            The Vacancy, or ``None`` when it is unknown or not waiting.
        """
        statement = select_waiting_vacancies().where(*match_vacancy_key(key))
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        return None if row is None else WaitingVacancy(key, row.fields, row.description)

    async def save_assessment(self, assessment: Assessment) -> None:
        """Save an Assessment, so its Vacancy no longer waits.

        Args:
            assessment: The Assessment to save.

        Raises:
            VacancyNotFoundError: If the Vacancy is unknown.
        """
        await self.save_vacancy_row(
            assessment_table,
            assessment.key,
            profile_version=assessment.profile_version,
            assessed_at=assessment.assessed_at,
            **assessment.result.model_dump(),
        )
        logger.debug("Saved Assessment of Vacancy %s", assessment.key)

    async def save_prefilter_rejection(self, rejection: PrefilterRejection) -> None:
        """Save a Pre-filter rejection, so its Vacancy no longer waits.

        Args:
            rejection: The rejection to save.

        Raises:
            VacancyNotFoundError: If the Vacancy is unknown.
        """
        await self.save_vacancy_row(
            prefilter_rejection_table,
            rejection.key,
            reason=rejection.reason,
            profile_version=rejection.profile_version,
            filtered_at=rejection.filtered_at,
        )
        logger.debug("Saved Pre-filter rejection of Vacancy %s", rejection.key)

    async def load_status(self, key: VacancyKey) -> VacancyStatus | None:
        """Load the status of one Vacancy.

        Args:
            key: Identity of the Vacancy.

        Returns:
            Its status, or ``None`` when the Vacancy is unknown.
        """
        statement = select_vacancies(vacancy_table.c.status).where(*match_vacancy_key(key))
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        return None if row is None else row.status

    async def save_status(self, key: VacancyKey, status: VacancyStatus) -> None:
        """Save the new status of a known Vacancy.

        Args:
            key: Identity of the Vacancy.
            status: Its new status.

        Raises:
            VacancyNotFoundError: If the Vacancy is unknown.
        """
        async with self.engine.begin() as connection:
            vacancy_id = await find_vacancy_id(connection, key)
            await connection.execute(
                update(vacancy_table).where(vacancy_table.c.id == vacancy_id).values(status=status)
            )
        logger.debug("Saved status %s of Vacancy %s", status, key)

    async def load_recent_assessed(self, limit: int) -> list[AssessedVacancy]:
        """Load the most recently Assessed Vacancies, whatever their score.

        Args:
            limit: How many Vacancies to load at most.

        Returns:
            The Vacancies, the most recently assessed first.
        """
        assessment = assessment_table.c
        statement = (
            select_vacancies(vacancy_table.c.fields, vacancy_table.c.status, assessment_table)
            .join(assessment_table, assessment.vacancy_id == vacancy_table.c.id)
            .order_by(assessment.assessed_at.desc(), assessment.vacancy_id.desc())
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).all()
        return [
            AssessedVacancy(
                row.fields,
                Assessment(
                    key=VacancyKey(row.name, row.site_id),
                    result=AssessmentResult(
                        score=row.score,
                        verdict=row.verdict,
                        strengths=row.strengths,
                        gaps=row.gaps,
                        exclusions=row.exclusions,
                    ),
                    profile_version=row.profile_version,
                    assessed_at=row.assessed_at,
                ),
                row.status,
            )
            for row in rows
        ]

    async def load_shortlisted_vacancy(self, key: VacancyKey) -> ShortlistedVacancy | None:
        """Load one Vacancy if it is shortlisted.

        Args:
            key: Identity of the Vacancy.

        Returns:
            The Vacancy, or ``None`` when it is unknown or not shortlisted.
        """
        vacancy = vacancy_table.c
        statement = select_vacancies(vacancy.source_id, vacancy.fields, vacancy.description).where(
            *match_vacancy_key(key), vacancy.status == "shortlisted"
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        if row is None:
            return None
        return ShortlistedVacancy(key, row.source_id, row.fields, row.description)

    async def save_application_pack(self, pack: ApplicationPack) -> None:
        """Save the metadata of an Application Pack.

        Args:
            pack: The metadata to save.

        Raises:
            VacancyNotFoundError: If the Vacancy is unknown.
        """
        await self.save_vacancy_row(
            application_pack_table,
            pack.key,
            profile_version=pack.profile_version,
            language=pack.language,
            tailored_at=pack.tailored_at,
        )
        logger.debug("Saved Application Pack of Vacancy %s", pack.key)

    async def save_vacancy_row(self, table: Table, key: VacancyKey, **values: object) -> None:
        """Save one row about a known Vacancy.

        A table keyed by the Vacancy holds one row per Vacancy, so saving
        again replaces it; other tables keep every saved row.

        Args:
            table: The table with a ``vacancy_id`` column.
            key: Identity of the Vacancy.
            **values: The row's other columns.

        Raises:
            VacancyNotFoundError: If the Vacancy is unknown.
        """
        async with self.engine.begin() as connection:
            row = {"vacancy_id": await find_vacancy_id(connection, key), **values}
            statement = insert(table).values(**row)
            if table.c.vacancy_id.primary_key:
                statement = statement.on_conflict_do_update(
                    index_elements=[table.c.vacancy_id], set_=values
                )
            await connection.execute(statement)


def select_vacancies(*columns: object) -> Select:
    """Select Vacancies with their Company name and site ID.

    Args:
        *columns: Further columns or tables to select.

    Returns:
        The statement over Vacancies joined to their Companies.
    """
    return (
        select(company_table.c.name, vacancy_table.c.site_id, *columns)
        .select_from(vacancy_table)
        .join(company_table, company_table.c.id == vacancy_table.c.company_id)
    )


def select_waiting_vacancies() -> Select:
    """Select the Vacancies waiting to be assessed, in the order they were recorded.

    Returns:
        The statement.
    """
    vacancy = vacancy_table.c
    return (
        select_vacancies(vacancy.fields, vacancy.description)
        .outerjoin(assessment_table, assessment_table.c.vacancy_id == vacancy.id)
        .outerjoin(prefilter_rejection_table, prefilter_rejection_table.c.vacancy_id == vacancy.id)
        .where(
            ~vacancy.is_baseline,
            ~vacancy.is_closed,
            vacancy.status == "new",
            assessment_table.c.vacancy_id.is_(None),
            prefilter_rejection_table.c.vacancy_id.is_(None),
        )
        .order_by(vacancy.id)
    )


def match_vacancy_key(key: VacancyKey) -> tuple[ColumnElement[bool], ...]:
    """Build the conditions that pick one Vacancy in ``select_vacancies``.

    Args:
        key: Identity of the Vacancy.

    Returns:
        The conditions on its Company name and site ID.
    """
    return company_table.c.name == key.company, vacancy_table.c.site_id == key.vacancy_id


async def find_vacancy_id(connection: AsyncConnection, key: VacancyKey) -> int:
    """Find the row id of a known Vacancy.

    Args:
        connection: An open database connection.
        key: Identity of the Vacancy.

    Returns:
        The row id.

    Raises:
        VacancyNotFoundError: If the Vacancy is unknown.
    """
    statement = select_vacancies(vacancy_table.c.id).where(*match_vacancy_key(key))
    row = (await connection.execute(statement)).first()
    if row is None:
        raise_vacancy_not_found(key.company, {key.vacancy_id})
    return row.id


async def load_latest_successful_runs(
    connection: AsyncConnection, company: str
) -> dict[str, RunRecord]:
    """Load the latest Scrape Run that is not suspect of each Source of a Company.

    Args:
        connection: An open database connection.
        company: The Company.

    Returns:
        The run, without its changes, by Source id.
    """
    run = scrape_run_table.c
    statement = (
        select(run.id, run.source_id, run.started_at, run.status, run.error)
        .join(source_table, source_table.c.id == run.source_id)
        .join(company_table, company_table.c.id == source_table.c.company_id)
        .where(company_table.c.name == company, run.status != "suspect")
        .ext(distinct_on(run.source_id))
        .order_by(run.source_id, run.started_at.desc(), run.id.desc())
    )
    rows = (await connection.execute(statement)).all()
    listings = await connection.execute(
        select(run_listing_table.c.run_id, run_listing_table.c.site_id).where(
            run_listing_table.c.run_id.in_([row.id for row in rows])
        )
    )
    listed_ids: dict[int, set[str]] = {row.id: set() for row in rows}
    for run_id, site_id in listings:
        listed_ids[run_id].add(site_id)
    return {
        row.source_id: RunRecord(
            source_id=row.source_id,
            company=company,
            started_at=row.started_at,
            status=row.status,
            listed_ids=frozenset(listed_ids[row.id]),
            error=row.error,
            new_vacancies=(),
        )
        for row in rows
    }


async def load_known_vacancies(
    connection: AsyncConnection, company: str
) -> dict[str, KnownVacancy]:
    """Load every Vacancy already recorded for a Company.

    Args:
        connection: An open database connection.
        company: The Company.

    Returns:
        The latest known fields and closed state, by site ID.
    """
    vacancy = vacancy_table.c
    statement = select_vacancies(vacancy.fields, vacancy.is_closed).where(
        company_table.c.name == company
    )
    rows = await connection.execute(statement)
    return {row.site_id: KnownVacancy(row.fields, row.is_closed) for row in rows}


async def save_company_source(connection: AsyncConnection, company: str, source_id: str) -> int:
    """Mirror a Company and one of its Sources from the config.

    Args:
        connection: A connection inside the run's transaction.
        company: The Company's name.
        source_id: The Source's id.

    Returns:
        The Company's row id.
    """
    await connection.execute(insert(company_table).values(name=company).on_conflict_do_nothing())
    company_id = (
        await connection.execute(select(company_table.c.id).where(company_table.c.name == company))
    ).scalar_one()
    await connection.execute(
        insert(source_table)
        .values(id=source_id, company_id=company_id)
        .on_conflict_do_update(index_elements=[source_table.c.id], set_={"company_id": company_id})
    )
    return company_id


async def insert_scrape_run(connection: AsyncConnection, record: RunRecord) -> int:
    """Insert a Scrape Run and the IDs its list holds.

    Args:
        connection: A connection inside the run's transaction.
        record: The run.

    Returns:
        The run's row id.
    """
    run_id = (
        await connection.execute(
            insert(scrape_run_table)
            .values(
                source_id=record.source_id,
                started_at=record.started_at,
                status=record.status,
                error=record.error,
            )
            .returning(scrape_run_table.c.id)
        )
    ).scalar_one()
    if record.listed_ids:
        await connection.execute(
            insert(run_listing_table),
            [{"run_id": run_id, "site_id": site_id} for site_id in record.listed_ids],
        )
    return run_id


async def update_company_vacancies(
    connection: AsyncConnection,
    company: str,
    company_id: int,
    site_ids: Collection[str],
    **values: object,
) -> list[int]:
    """Update known Vacancies of one Company.

    Args:
        connection: A connection inside the run's transaction.
        company: The Company's name, for the error.
        company_id: The Company's row id.
        site_ids: Site IDs of the Vacancies to update.
        **values: The columns to set.

    Returns:
        Row ids of the updated Vacancies.

    Raises:
        VacancyNotFoundError: If any of the Vacancies is unknown.
    """
    vacancy = vacancy_table.c
    statement = (
        update(vacancy_table)
        .where(vacancy.company_id == company_id, vacancy.site_id.in_(site_ids))
        .values(**values)
        .returning(vacancy.id, vacancy.site_id)
    )
    rows = (await connection.execute(statement)).all()
    missing = set(site_ids) - {row.site_id for row in rows}
    if missing:
        raise_vacancy_not_found(company, missing)
    return [row.id for row in rows]


def raise_vacancy_not_found(company: str, site_ids: Collection[str]) -> NoReturn:
    """Log and raise that Vacancies of a Company are unknown.

    Args:
        company: The Company.
        site_ids: Site IDs of the unknown Vacancies.

    Raises:
        VacancyNotFoundError: Always.
    """
    names = ", ".join(sorted(site_ids))
    logger.error("Unknown Vacancy %s of %s", names, company)
    raise VacancyNotFoundError(f"unknown Vacancy {names} of {company}")
