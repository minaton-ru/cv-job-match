"""Technique adapter for Sources whose list is server-side HTML read with CSS selectors."""

import logging

from bs4 import Tag
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cv_job_match.adapters.common import (
    CssSelector,
    PageDetailOptions,
    PageRequestOptions,
    RequiredCssSelector,
    fail_source_fetch,
    fetch_html_page,
    normalize_field_value,
    parse_technique_options,
)
from cv_job_match.discovery import ListedVacancy
from cv_job_match.http_client import HttpClient
from cv_job_match.sources import RequiredText, SourceConfig

logger = logging.getLogger(__name__)


class SelectedValue(BaseModel):
    """Where a value is inside a list item.

    Plain text in the config is taken as ``selector``.

    Attributes:
        selector: CSS selector of the element inside the item; empty for the
            item itself. The first match is used.
        attribute: Attribute to read, or ``None`` for the element's text.
    """

    model_config = ConfigDict(frozen=True)

    selector: CssSelector = ""
    attribute: RequiredText | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_plain_selector(cls, data: object) -> object:
        """Read plain text as the selector of the element's text.

        Args:
            data: Raw value from the config.

        Returns:
            A mapping with ``selector`` for text, other values unchanged.
        """
        return {"selector": data} if isinstance(data, str) else data


class HtmlListOptions(PageRequestOptions):
    """How to fetch and read a Source's HTML list.

    Attributes:
        list_selector: CSS selector of the element that holds the list. The
            page must contain it.
        item_selector: CSS selector of each item inside the list element.
        id_field: Where the site's own ID is inside an item.
        fields: Field name to where its value is inside an item.
    """

    list_selector: RequiredCssSelector
    item_selector: RequiredCssSelector
    id_field: SelectedValue
    fields: dict[str, SelectedValue] = Field(default_factory=dict)


class HtmlDetailOptions(PageDetailOptions):
    """How to fetch a Vacancy's description from its HTML detail page.

    Attributes:
        description_selectors: CSS selectors of the parts of the description.
    """

    description_selectors: list[RequiredCssSelector] = Field(min_length=1)


class HtmlCssOptions(BaseModel):
    """Technique options of an ``html_css`` Source.

    Attributes:
        list_options: How to fetch and read the list.
        detail_options: How to fetch descriptions, if configured.
    """

    model_config = ConfigDict(frozen=True)

    list_options: HtmlListOptions = Field(alias="list")
    detail_options: HtmlDetailOptions | None = Field(default=None, alias="detail")


class HtmlCssAdapter:
    """Reads Sources whose list is server-side HTML."""

    def __init__(self, http: HttpClient) -> None:
        """Create the adapter.

        Args:
            http: Client every request goes through.
        """
        self.http = http

    async def fetch_listed_vacancies(self, source: SourceConfig) -> list[ListedVacancy]:
        """Fetch a Source's HTML list page and read its items.

        Args:
            source: The Source to read.

        Returns:
            Every listed Vacancy, in page order.

        Raises:
            SourceFetchError: If the page cannot be fetched, has no list
                element, or an item has no ID.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_technique_options(source, HtmlCssOptions).list_options
        page = await fetch_html_page(self.http, source, options.build_page_request())
        list_element = page.select_one(options.list_selector)
        if list_element is None:
            fail_source_fetch(
                source, f"no element matches list selector {options.list_selector!r}"
            )
        items = list_element.select(options.item_selector)
        logger.debug("Source %s: list holds %d items", source.id, len(items))
        return [read_html_vacancy(source, options, item) for item in items]

    async def fetch_vacancy_description(
        self, source: SourceConfig, vacancy: ListedVacancy
    ) -> str | None:
        """Fetch a Vacancy's description from its HTML detail page.

        Args:
            source: The Source that lists the Vacancy.
            vacancy: The listed Vacancy.

        Returns:
            The text of every element the description selectors match, joined
            by blank lines, or ``None`` when the Source has no ``detail``
            options.

        Raises:
            SourceFetchError: If the page cannot be fetched, or no description
                selector matches an element with text.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_technique_options(source, HtmlCssOptions).detail_options
        if options is None:
            return None
        request = options.build_detail_request(vacancy.vacancy_id)
        page = await fetch_html_page(self.http, source, request)
        texts = [
            element.get_text("\n", strip=True)
            for selector in options.description_selectors
            for element in page.select(selector)
        ]
        description = "\n\n".join(text for text in texts if text)
        if not description:
            fail_source_fetch(source, f"no description found for Vacancy {vacancy.vacancy_id}")
        return description


def read_html_vacancy(source: SourceConfig, options: HtmlListOptions, item: Tag) -> ListedVacancy:
    """Read one HTML list item into a listed Vacancy.

    Args:
        source: The Source the item comes from.
        options: The Source's list options.
        item: One item element.

    Returns:
        The listed Vacancy.

    Raises:
        SourceFetchError: If the item has no ID, or its ID is blank.
    """
    vacancy_id = read_selected_value(item, options.id_field)
    if vacancy_id is None:
        fail_source_fetch(
            source, f"list item has no ID at {describe_selected_value(options.id_field)}"
        )
    fields = {name: read_selected_value(item, value) for name, value in options.fields.items()}
    return ListedVacancy(vacancy_id=vacancy_id, fields=fields)


def read_selected_value(item: Tag, selected: SelectedValue) -> str | None:
    """Read a value from inside an item.

    Args:
        item: The item element.
        selected: Where the value is.

    Returns:
        The attribute value or the text with whitespace collapsed, or ``None``
        when the element or attribute is missing or the value is blank.
    """
    element = item.select_one(selected.selector) if selected.selector else item
    if element is None:
        return None
    if selected.attribute is None:
        return normalize_field_value(" ".join(element.get_text(" ").split()))
    value = element.get(selected.attribute)
    return normalize_field_value(" ".join(value) if isinstance(value, list) else value)


def describe_selected_value(selected: SelectedValue) -> str:
    """Describe where a value is, for error messages.

    Args:
        selected: Where the value is.

    Returns:
        Text such as ``'a' attribute 'data-id'`` or ``the item's text``.
    """
    place = repr(selected.selector) if selected.selector else "the item"
    return f"{place} attribute {selected.attribute!r}" if selected.attribute else f"{place} text"
