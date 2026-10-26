"""Discovery: checking Sources for Vacancies."""

import logging
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from cv_job_match.constants import DESCRIPTION_PREVIEW_CHARS, PREVIEW_COUNT
from cv_job_match.exceptions import (
    SourceFetchError,
    UnknownSourceError,
    UnsupportedTechniqueError,
)
from cv_job_match.sources import SourceConfig, SourcesConfig, Technique

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
        self, sources: SourcesConfig, adapters: Mapping[Technique, TechniqueAdapter]
    ) -> None:
        """Create the Discovery module.

        Args:
            sources: The Sources config.
            adapters: Technique adapters by technique name.
        """
        self.sources = sources
        self.adapters = adapters

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
        title=vacancy.fields.get("title"),
        url=vacancy.fields.get("url"),
        description=description,
        description_error=description_error,
    )
