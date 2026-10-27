"""Technique adapter for Sources whose list is framework state embedded in the page."""

import json
import logging
import re

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field

from cv_job_match.adapters.common import (
    JsonItemsOptions,
    PageDetailOptions,
    PageRequestOptions,
    RequiredCssSelector,
    fail_source_fetch,
    fetch_html_page,
    normalize_field_value,
    parse_technique_options,
    read_json_path,
    read_list_items,
    read_listed_vacancy,
)
from cv_job_match.discovery import ListedVacancy
from cv_job_match.http_client import HttpClient
from cv_job_match.sources import RequiredText, SourceConfig

logger = logging.getLogger(__name__)


class StateLocationOptions(BaseModel):
    """Where the embedded state is in a page.

    Attributes:
        script_selector: CSS selector of the script element holding the state.
        assignment: Name the script assigns the state to, such as
            ``window.__STATE__``, or ``None`` when the script is pure JSON.
    """

    model_config = ConfigDict(frozen=True)

    script_selector: RequiredCssSelector
    assignment: RequiredText | None = None


class EmbeddedListOptions(PageRequestOptions, StateLocationOptions, JsonItemsOptions):
    """How to fetch a Source's list page and read the items in its state."""


class EmbeddedDetailOptions(PageDetailOptions, StateLocationOptions):
    """How to fetch a Vacancy's description from the state of its detail page.

    Attributes:
        description_fields: Dotted paths, inside the state, to the parts of
            the description.
    """

    description_fields: list[RequiredText] = Field(min_length=1)


class EmbeddedStateOptions(BaseModel):
    """Technique options of an ``embedded_state`` Source.

    Attributes:
        list_options: How to fetch and read the list.
        detail_options: How to fetch descriptions, if configured.
    """

    model_config = ConfigDict(frozen=True)

    list_options: EmbeddedListOptions = Field(alias="list")
    detail_options: EmbeddedDetailOptions | None = Field(default=None, alias="detail")


class EmbeddedStateAdapter:
    """Reads Sources whose list is framework state embedded in the page."""

    def __init__(self, http: HttpClient) -> None:
        """Create the adapter.

        Args:
            http: Client every request goes through.
        """
        self.http = http

    async def fetch_listed_vacancies(self, source: SourceConfig) -> list[ListedVacancy]:
        """Fetch a Source's list page and read the items in its embedded state.

        Args:
            source: The Source to read.

        Returns:
            Every listed Vacancy, in list order.

        Raises:
            SourceFetchError: If the page cannot be fetched, its state is
                missing or malformed, the items path does not hold a list, or
                an item has no ID.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_technique_options(source, EmbeddedStateOptions).list_options
        page = await fetch_html_page(self.http, source, options.build_page_request())
        items = read_list_items(source, options, read_embedded_state(source, options, page))
        logger.debug("Source %s: state holds %d items", source.id, len(items))
        return [read_listed_vacancy(source, options, item) for item in items]

    async def fetch_vacancy_description(
        self, source: SourceConfig, vacancy: ListedVacancy
    ) -> str | None:
        """Fetch a Vacancy's description from the state of its detail page.

        Args:
            source: The Source that lists the Vacancy.
            vacancy: The listed Vacancy.

        Returns:
            The description parts joined by blank lines, or ``None`` when the
            Source has no ``detail`` options.

        Raises:
            SourceFetchError: If the page cannot be fetched, its state is
                missing or malformed, or none of the description fields holds
                text.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_technique_options(source, EmbeddedStateOptions).detail_options
        if options is None:
            return None
        request = options.build_detail_request(vacancy.vacancy_id)
        page = await fetch_html_page(self.http, source, request)
        state = read_embedded_state(source, options, page)
        parts = [
            normalize_field_value(read_json_path(state, path))
            for path in options.description_fields
        ]
        texts = [part for part in parts if part is not None]
        if not texts:
            fail_source_fetch(
                source, f"no description fields found for Vacancy {vacancy.vacancy_id}"
            )
        return "\n\n".join(texts)


def read_embedded_state(
    source: SourceConfig, location: StateLocationOptions, page: BeautifulSoup
) -> object:
    """Parse the state a page embeds in a script element.

    Args:
        source: The Source the page comes from.
        location: Where the state is.
        page: The parsed page.

    Returns:
        The parsed state.

    Raises:
        SourceFetchError: If no script matches, the assignment is not in it,
            or the state is not valid JSON.
    """
    script = page.select_one(location.script_selector)
    if script is None:
        fail_source_fetch(
            source, f"no element matches script selector {location.script_selector!r}"
        )
    text = script.get_text().strip()
    start = 0
    if location.assignment is not None:
        match = re.search(rf"(?<![\w$]){re.escape(location.assignment)}\s*=(?!=)\s*", text)
        if match is None:
            fail_source_fetch(source, f"state {location.assignment!r} not found in script")
        start = match.end()
    try:
        state, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        fail_source_fetch(source, f"malformed state in {location.script_selector!r}")
    return state
