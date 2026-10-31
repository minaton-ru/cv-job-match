"""Discovery: checking Sources for Vacancies."""

import asyncio
import logging
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from cv_job_match.constants import (
    DESCRIPTION_FIELD,
    DESCRIPTION_PREVIEW_CHARS,
    PREVIEW_COUNT,
    TITLE_FIELD,
)
from cv_job_match.exceptions import (
    RepositoryNotConfiguredError,
    SourceFetchError,
    SourcesConfigError,
    UnknownSourceError,
    UnsupportedTechniqueError,
)
from cv_job_match.sources import SourceConfig, SourcesConfig, Technique
from cv_job_match.utils.clock import Clock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ListedVacancy:
    """A Vacancy as it appears in a Source's list.

    Attributes:
        vacancy_id: The site's own ID for the Vacancy.
        fields: Mapped fields, such as ``title`` and ``url``. A field missing
            from the item is ``None``.
    """

    vacancy_id: str
    fields: Mapping[str, str | None]


RunStatus = Literal["ok", "baseline", "suspect"]


@dataclass(frozen=True)
class NewVacancy:
    """A Vacancy recorded for the first time by a Scrape Run.

    Attributes:
        vacancy_id: The site's own ID for the Vacancy.
        fields: Mapped fields from the Source's list.
        description: The full description, or ``None`` when the Source has none.
        is_baseline: Whether a Baseline Run recorded it, so it is never assessed.
    """

    vacancy_id: str
    fields: Mapping[str, str | None]
    description: str | None
    is_baseline: bool


@dataclass(frozen=True)
class Revision:
    """A change to a known Vacancy that is visible in a Source's list.

    Attributes:
        vacancy_id: The site's own ID for the Vacancy.
        fields: Only the fields that changed, with their new values.
    """

    vacancy_id: str
    fields: Mapping[str, str | None]


@dataclass(frozen=True)
class RunRecord:
    """One Scrape Run with every change it found, saved as one unit.

    Attributes:
        source_id: The checked Source.
        company: The Source's Company.
        started_at: When the run started.
        status: ``baseline`` for the first successful run, ``suspect`` for a
            Suspect Scrape Run, ``ok`` otherwise.
        listed_ids: IDs of the distinct Vacancies the list holds; empty for a
            suspect run.
        error: Why the run is suspect, or ``None``.
        new_vacancies: Vacancies recorded for the first time.
        revisions: Changes to known Vacancies.
        closed_ids: Vacancies that no Source of the Company lists any more.
        reopened_ids: Closed Vacancies that are listed again.
    """

    source_id: str
    company: str
    started_at: datetime
    status: RunStatus
    listed_ids: frozenset[str]
    error: str | None
    new_vacancies: tuple[NewVacancy, ...]
    revisions: tuple[Revision, ...] = ()
    closed_ids: frozenset[str] = frozenset()
    reopened_ids: frozenset[str] = frozenset()

    @property
    def found_count(self) -> int:
        """How many distinct Vacancies the list holds."""
        return len(self.listed_ids)


@dataclass(frozen=True)
class KnownVacancy:
    """A Vacancy already recorded for a Company.

    Attributes:
        fields: The latest known fields from a Source's list.
        is_closed: Whether it is a Closed Vacancy.
    """

    fields: Mapping[str, str | None]
    is_closed: bool


@dataclass(frozen=True)
class SourceState:
    """What is already known before a Source is checked.

    Attributes:
        source_id: The Source to check.
        company: The Source's Company.
        last_successful_run: The Source's last run that was not suspect, if any.
        known_vacancies: Every Vacancy already recorded for the Company, by site ID.
        listed_by_other_sources: IDs listed by the latest successful run of
            each other Source of the Company.
    """

    source_id: str
    company: str
    last_successful_run: RunRecord | None
    known_vacancies: Mapping[str, KnownVacancy]
    listed_by_other_sources: frozenset[str] = frozenset()


class DiscoveryRepository(Protocol):
    """Storage of Scrape Runs and the Vacancies they found."""

    async def load_state(self, source_id: str, company: str) -> SourceState:
        """Load what is known before a Source is checked.

        Args:
            source_id: The Source to check.
            company: The Source's Company.

        Returns:
            The Source's last successful run, the Company's known Vacancies
            and what its other Sources list.
        """
        ...

    async def load_last_runs(self) -> Mapping[str, datetime]:
        """Load when each Source last ran.

        Returns:
            Start time of each Source's latest Scrape Run of any status, by
            Source id. Sources that never ran are missing.
        """
        ...

    async def save_run(self, record: RunRecord) -> None:
        """Save a Scrape Run together with all its changes, or nothing at all.

        Args:
            record: The run to save.
        """
        ...


@dataclass(frozen=True)
class DiscoveryReport:
    """The outcome of one ``run_due`` tick.

    Attributes:
        runs: Every Scrape Run of the tick.
    """

    runs: tuple[RunRecord, ...]


class TechniqueAdapter(Protocol):
    """Reads Sources that use one technique."""

    async def fetch_listed_vacancies(self, source: SourceConfig) -> list[ListedVacancy]:
        """Fetch the Vacancies a Source lists.

        Args:
            source: The Source to read.

        Returns:
            Every listed Vacancy, in list order.

        Raises:
            SourceFetchError: If the list cannot be fetched or read.
            SourcesConfigError: If the Source's technique options are invalid.
        """
        ...

    async def fetch_vacancy_description(
        self, source: SourceConfig, vacancy: ListedVacancy
    ) -> str | None:
        """Fetch the full description of one listed Vacancy.

        Args:
            source: The Source that lists the Vacancy.
            vacancy: The listed Vacancy.

        Returns:
            The description, or ``None`` when the Source has no description
            configured.

        Raises:
            SourceFetchError: If the description cannot be fetched or read.
            SourcesConfigError: If the Source's technique options are invalid.
        """
        ...


@dataclass(frozen=True)
class VacancyPreview:
    """A short view of one found Vacancy.

    Attributes:
        vacancy_id: The site's own ID for the Vacancy.
        title: The Vacancy's title, if mapped.
        url: Link to the Vacancy, if mapped.
        description: Start of the description, if one was fetched.
        description_error: Why the description could not be fetched, if it failed.
    """

    vacancy_id: str
    title: str | None
    url: str | None
    description: str | None
    description_error: str | None


@dataclass(frozen=True)
class RunSummary:
    """The outcome of checking one Source.

    Attributes:
        source_id: The checked Source.
        found_count: How many Vacancies the list holds.
        previews: Short views of the first few Vacancies.
        suspect_reason: Why the run is suspect, or ``None`` when it is not.
    """

    source_id: str
    found_count: int
    previews: tuple[VacancyPreview, ...]
    suspect_reason: str | None


class Discovery:
    """Checks Sources for Vacancies."""

    def __init__(
        self,
        sources: SourcesConfig,
        adapters: Mapping[Technique, TechniqueAdapter],
        repository: DiscoveryRepository | None,
        clock: Clock,
    ) -> None:
        """Create the Discovery module.

        Args:
            sources: The Sources config.
            adapters: Technique adapters by technique name.
            repository: Storage of Scrape Runs, or ``None`` when the app has
                none yet; then only ``check_source`` works.
            clock: Source of the current time.
        """
        self.sources = sources
        self.adapters = adapters
        self.repository = repository
        self.clock = clock

    async def run_due(self) -> DiscoveryReport:
        """Check every enabled Source that is due and save each Scrape Run.

        A Source is due when it never ran, or when its interval has passed
        since its last Scrape Run of any status. Sources of one Company run
        one after another, and different Companies run concurrently. Site and
        network failures, and invalid technique options, become suspect runs.

        Returns:
            Every Scrape Run of this tick.

        Raises:
            RepositoryNotConfiguredError: If the app was built without a
                Discovery repository.
        """
        repository = self.require_repository()
        last_runs = await repository.load_last_runs()
        now = self.clock.now_utc()
        companies: dict[str, list[SourceConfig]] = {}
        for source in self.sources.sources:
            if source.enabled and self.is_source_due(source, last_runs.get(source.id), now):
                companies.setdefault(source.company, []).append(source)
        logger.info("Running %d due Sources", sum(map(len, companies.values())))
        results = await asyncio.gather(
            *(self.run_company_sources(repository, sources) for sources in companies.values())
        )
        return DiscoveryReport(tuple(run for runs in results for run in runs))

    def require_repository(self) -> DiscoveryRepository:
        """Return the Discovery repository.

        Returns:
            The repository.

        Raises:
            RepositoryNotConfiguredError: If the app was built without one.
        """
        if self.repository is None:
            logger.error("Discovery has no repository")
            raise RepositoryNotConfiguredError("Discovery needs a repository to run Sources")
        return self.repository

    def is_source_due(
        self, source: SourceConfig, last_run_at: datetime | None, now: datetime
    ) -> bool:
        """Tell whether a Source should be checked now.

        Args:
            source: The Source.
            last_run_at: Start of its latest Scrape Run, or ``None`` if it never ran.
            now: The current time.

        Returns:
            Whether it never ran or its interval has passed.
        """
        return last_run_at is None or now - last_run_at >= self.sources.resolve_interval(source)

    async def run_company_sources(
        self, repository: DiscoveryRepository, sources: Sequence[SourceConfig]
    ) -> list[RunRecord]:
        """Run the due Sources of one Company one after another.

        Args:
            repository: Where each run is saved.
            sources: The Company's due Sources, in config order.

        Returns:
            The run of each Source, in the same order.
        """
        return [await self.run_one_source(repository, source) for source in sources]

    async def run_one_source(
        self, repository: DiscoveryRepository, source: SourceConfig
    ) -> RunRecord:
        """Check one Source and save its Scrape Run.

        Args:
            repository: Where the run is saved.
            source: The Source to check.

        Returns:
            The saved run.
        """
        adapter = self.find_adapter(source)
        state = await repository.load_state(source.id, source.company)
        started_at = self.clock.now_utc()
        listed: list[ListedVacancy] = []
        texts: dict[str, str | None] = {}
        error = None
        try:
            listed = await adapter.fetch_listed_vacancies(source)
            texts = await fetch_planned_descriptions(adapter, source, listed, state)
        except (SourceFetchError, SourcesConfigError) as fetch_error:
            error = str(fetch_error)
        record = detect_changes(state, listed, error, texts, started_at)
        await repository.save_run(record)
        logger.info(
            "Scrape Run of Source %s: %s, found %d, new %d, revised %d, closed %d, "
            "reopened %d, error: %s",
            source.id,
            record.status,
            record.found_count,
            len(record.new_vacancies),
            len(record.revisions),
            len(record.closed_ids),
            len(record.reopened_ids),
            record.error,
        )
        return record

    async def check_source(self, source_id: str) -> RunSummary:
        """Dry-run one Source without saving anything.

        Fetches the list and the descriptions of the first few Vacancies. A
        failed list fetch is reported as the suspect reason, not raised.

        Args:
            source_id: Id of the Source to check. Switched-off Sources can be
                checked too.

        Returns:
            The count found, previews of the first few Vacancies and any
            suspect reason.

        Raises:
            UnknownSourceError: If no Source has this id.
            UnsupportedTechniqueError: If no adapter handles the Source's technique.
            SourcesConfigError: If the Source's technique options are invalid.
        """
        source = self.find_source(source_id)
        adapter = self.find_adapter(source)
        logger.info("Checking Source %s", source.id)
        try:
            listed = await adapter.fetch_listed_vacancies(source)
        except SourceFetchError as error:
            logger.info("Source %s is suspect: %s", source.id, error)
            return RunSummary(source.id, 0, (), str(error))
        previews = [
            await preview_vacancy(adapter, source, vacancy) for vacancy in listed[:PREVIEW_COUNT]
        ]
        logger.info("Source %s lists %d Vacancies", source.id, len(listed))
        return RunSummary(source.id, len(listed), tuple(previews), None)

    def find_source(self, source_id: str) -> SourceConfig:
        """Find a Source by id.

        Args:
            source_id: Id of the Source.

        Returns:
            The Source.

        Raises:
            UnknownSourceError: If no Source has this id.
        """
        for source in self.sources.sources:
            if source.id == source_id:
                return source
        logger.error("Unknown Source id %r", source_id)
        raise UnknownSourceError(f"unknown Source {source_id!r}")

    def find_adapter(self, source: SourceConfig) -> TechniqueAdapter:
        """Find the adapter for a Source's technique.

        Args:
            source: The Source.

        Returns:
            The adapter.

        Raises:
            UnsupportedTechniqueError: If no adapter handles the technique yet.
        """
        adapter = self.adapters.get(source.technique)
        if adapter is None:
            logger.error("No adapter for technique %s of Source %s", source.technique, source.id)
            raise UnsupportedTechniqueError(
                f"Source {source.id!r}: technique {source.technique!r} is not supported yet"
            )
        return adapter


async def preview_vacancy(
    adapter: TechniqueAdapter, source: SourceConfig, vacancy: ListedVacancy
) -> VacancyPreview:
    """Fetch a Vacancy's description and build its preview.

    Args:
        adapter: Adapter for the Source's technique.
        source: The Source that lists the Vacancy.
        vacancy: The listed Vacancy.

    Returns:
        The preview. A failed description fetch is noted, not raised.
    """
    description, description_error = None, None
    try:
        text = await adapter.fetch_vacancy_description(source, vacancy)
    except SourceFetchError as error:
        description_error = str(error)
    else:
        if text is not None:
            description = textwrap.shorten(text, DESCRIPTION_PREVIEW_CHARS, placeholder="…")
    return VacancyPreview(
        vacancy_id=vacancy.vacancy_id,
        title=vacancy.fields.get(TITLE_FIELD),
        url=vacancy.fields.get("url"),
        description=description,
        description_error=description_error,
    )


def plan_detail_fetches(listed: Sequence[ListedVacancy], state: SourceState) -> list[str]:
    """Choose which descriptions a Scrape Run must fetch.

    A Baseline Run fetches none, because its Vacancies are never assessed.

    Args:
        listed: The Vacancies in the Source's list, in list order.
        state: What is already known about the Source and its Company.

    Returns:
        IDs of new Vacancies whose list item lacks the full text, each once,
        in list order.
    """
    if state.last_successful_run is None:
        return []
    return [
        vacancy.vacancy_id
        for vacancy in deduplicate_listed(listed)
        if vacancy.vacancy_id not in state.known_vacancies
        and vacancy.fields.get(DESCRIPTION_FIELD) is None
    ]


def deduplicate_listed(listed: Sequence[ListedVacancy]) -> list[ListedVacancy]:
    """Keep the first item of every Vacancy ID in a list.

    Args:
        listed: The Vacancies in the Source's list, in list order.

    Returns:
        One item per Vacancy ID, in list order.
    """
    unique: dict[str, ListedVacancy] = {}
    for vacancy in listed:
        unique.setdefault(vacancy.vacancy_id, vacancy)
    return list(unique.values())


def detect_changes(
    state: SourceState,
    listed: Sequence[ListedVacancy],
    error: str | None,
    texts: Mapping[str, str | None],
    now: datetime,
) -> RunRecord:
    """Turn one fetched Source list into a Scrape Run with its changes.

    Args:
        state: What was known before the run.
        listed: The Vacancies in the Source's list, in list order.
        error: Why the list fetch failed, or ``None`` when it succeeded.
        texts: Fetched descriptions by Vacancy ID, for the IDs that
            ``plan_detail_fetches`` chose. A missing ID means its fetch failed.
        now: When the run started.

    Returns:
        The run record. A failed list fetch, or an empty list when the last
        successful run found Vacancies, gives a ``suspect`` run with no
        changes. The first successful run is the ``baseline``.
    """
    unique = deduplicate_listed(listed)
    suspect_reason = error or find_empty_list_reason(state, unique)
    if suspect_reason is not None:
        return RunRecord(
            source_id=state.source_id,
            company=state.company,
            started_at=now,
            status="suspect",
            listed_ids=frozenset(),
            error=suspect_reason,
            new_vacancies=(),
        )
    is_baseline = state.last_successful_run is None
    planned_ids = set(plan_detail_fetches(listed, state))
    new_vacancies: list[NewVacancy] = []
    revisions: list[Revision] = []
    reopened_ids: set[str] = set()
    for vacancy in unique:
        vacancy_id = vacancy.vacancy_id
        known = state.known_vacancies.get(vacancy_id)
        if known is not None:
            changed = find_changed_fields(known.fields, vacancy.fields)
            if changed:
                revisions.append(Revision(vacancy_id, changed))
            if known.is_closed:
                reopened_ids.add(vacancy_id)
            continue
        if vacancy_id not in planned_ids:
            description = vacancy.fields.get(DESCRIPTION_FIELD)
        elif vacancy_id in texts:
            description = texts[vacancy_id]
        else:
            logger.debug("Left out Vacancy %s of Source %s for now", vacancy_id, state.source_id)
            continue
        new_vacancies.append(NewVacancy(vacancy_id, vacancy.fields, description, is_baseline))
    status: RunStatus = "baseline" if is_baseline else "ok"
    listed_ids = frozenset(vacancy.vacancy_id for vacancy in unique)
    return RunRecord(
        source_id=state.source_id,
        company=state.company,
        started_at=now,
        status=status,
        listed_ids=listed_ids,
        error=None,
        new_vacancies=tuple(new_vacancies),
        revisions=tuple(revisions),
        closed_ids=find_closed_ids(state, listed_ids),
        reopened_ids=frozenset(reopened_ids),
    )


def find_empty_list_reason(state: SourceState, unique: Sequence[ListedVacancy]) -> str | None:
    """Tell why an empty list is suspect, if it is.

    Args:
        state: What was known before the run.
        unique: The distinct Vacancies in the Source's list.

    Returns:
        The reason when the list is empty and the last successful run found
        Vacancies, or ``None``.
    """
    last_run = state.last_successful_run
    if unique or last_run is None or last_run.found_count == 0:
        return None
    reason = f"found 0 Vacancies, the last successful run found {last_run.found_count}"
    logger.debug("Source %s is suspect: %s", state.source_id, reason)
    return reason


def find_closed_ids(state: SourceState, listed_ids: frozenset[str]) -> frozenset[str]:
    """Find the open Vacancies that no Source of the Company lists any more.

    Args:
        state: What was known before the run.
        listed_ids: IDs in this Source's list now.

    Returns:
        IDs of the Vacancies to close.
    """
    still_listed = listed_ids | state.listed_by_other_sources
    return frozenset(
        vacancy_id
        for vacancy_id, known in state.known_vacancies.items()
        if not known.is_closed and vacancy_id not in still_listed
    )


def find_changed_fields(
    known_fields: Mapping[str, str | None], listed_fields: Mapping[str, str | None]
) -> dict[str, str | None]:
    """Find the list fields whose values differ from the known ones.

    Only fields the Source maps are compared, so a field that only another
    Source of the Company maps is never reported as changed.

    Args:
        known_fields: The latest known fields of the Vacancy.
        listed_fields: The fields in the Source's list now.

    Returns:
        Each changed field with its new value.
    """
    return {
        name: value for name, value in listed_fields.items() if known_fields.get(name) != value
    }


async def fetch_planned_descriptions(
    adapter: TechniqueAdapter,
    source: SourceConfig,
    listed: Sequence[ListedVacancy],
    state: SourceState,
) -> dict[str, str | None]:
    """Fetch the descriptions that ``plan_detail_fetches`` chose.

    Args:
        adapter: Adapter for the Source's technique.
        source: The Source that lists the Vacancies.
        listed: The Vacancies in the Source's list.
        state: What was known before the run.

    Returns:
        Description by Vacancy ID. A failed fetch leaves its ID out.

    Raises:
        SourcesConfigError: If the Source's detail options are invalid.
    """
    by_id = {vacancy.vacancy_id: vacancy for vacancy in listed}
    texts: dict[str, str | None] = {}
    for vacancy_id in plan_detail_fetches(listed, state):
        try:
            texts[vacancy_id] = await adapter.fetch_vacancy_description(source, by_id[vacancy_id])
        except SourceFetchError as error:
            logger.info(
                "Description of Vacancy %s of Source %s failed: %s", vacancy_id, source.id, error
            )
    return texts
