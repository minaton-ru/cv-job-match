"""Helpers shared by the technique adapters."""

import logging
from typing import Annotated, NoReturn, Self

import soupsieve
from bs4 import BeautifulSoup
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from cv_job_match.discovery import ListedVacancy
from cv_job_match.exceptions import HttpRequestError, SourceFetchError, SourcesConfigError
from cv_job_match.http_client import HttpClient, HttpRequest
from cv_job_match.sources import (
    RequiredText,
    SourceConfig,
    describe_error_detail,
    join_location_parts,
)

logger = logging.getLogger(__name__)

QueryParams = dict[str, str | int | float]


def check_css_selector(selector: str) -> str:
    """Reject a CSS selector with invalid syntax.

    Args:
        selector: Selector from the config; an empty one is allowed.

    Returns:
        The selector without surrounding whitespace.

    Raises:
        ValueError: If the selector has invalid syntax or uses a feature
            that cannot match elements, such as a pseudo-element.
    """
    stripped = selector.strip()
    if not stripped:
        return stripped
    try:
        soupsieve.compile(stripped)
    except (soupsieve.SelectorSyntaxError, NotImplementedError) as error:
        logger.warning("Rejected CSS selector %r: %s", stripped, error)
        raise ValueError(f"invalid CSS selector {stripped!r}") from error
    return stripped


CssSelector = Annotated[str, AfterValidator(check_css_selector)]
RequiredCssSelector = Annotated[RequiredText, AfterValidator(check_css_selector)]


class PageRequestOptions(BaseModel):
    """How to request an HTML page with GET.

    Attributes:
        url: Page URL.
        params: Query parameters.
    """

    model_config = ConfigDict(frozen=True)

    url: RequiredText
    params: QueryParams = Field(default_factory=dict)

    def build_page_request(self) -> HttpRequest:
        """Build the GET request for the page.

        Returns:
            The request with text query parameters.
        """
        return HttpRequest(url=self.url, params=format_query_params(self.params))


class PageDetailOptions(PageRequestOptions):
    """How to request a Vacancy's HTML detail page with GET.

    ``{id}`` in the URL or in a query parameter value is replaced with the
    Vacancy's ID.
    """

    @model_validator(mode="after")
    def check_vacancy_id_placeholder(self) -> Self:
        """Reject a detail request that does not depend on the Vacancy.

        Returns:
            The options unchanged.

        Raises:
            ValueError: If ``{id}`` is in neither the URL nor a params value.
        """
        if "{id}" not in self.url and not any(
            "{id}" in str(value) for value in self.params.values()
        ):
            logger.debug("Rejected detail options without {id}: %s", self.url)
            raise ValueError("{id} must appear in url or params")
        return self

    def build_detail_request(self, vacancy_id: str) -> HttpRequest:
        """Build the request for one Vacancy's detail page.

        Args:
            vacancy_id: The site's own ID for the Vacancy.

        Returns:
            The GET request with the ID filled in.
        """
        return HttpRequest(
            url=fill_vacancy_id(self.url, vacancy_id),
            params={
                key: fill_vacancy_id(value, vacancy_id)
                for key, value in format_query_params(self.params).items()
            },
        )


class JsonItemsOptions(BaseModel):
    """Where the items of a list are inside parsed JSON.

    Attributes:
        items_path: Dotted path to the list of items, empty for the top level.
        id_field: Dotted path, inside an item, to the site's own ID.
        fields: Field name to dotted path inside an item, such as ``title``.
    """

    model_config = ConfigDict(frozen=True)

    items_path: str
    id_field: RequiredText
    fields: dict[str, RequiredText] = Field(default_factory=dict)


def parse_technique_options[Options: BaseModel](
    source: SourceConfig, options_type: type[Options]
) -> Options:
    """Validate a Source's technique options.

    Args:
        source: The Source.
        options_type: Model of the options of the Source's technique.

    Returns:
        The validated options.

    Raises:
        SourcesConfigError: If the options are invalid. The message names the
            Source and the problem.
    """
    try:
        return options_type.model_validate(source.options)
    except ValidationError as error:
        problems = "; ".join(
            f"{join_location_parts(detail['loc'])}: {describe_error_detail(detail)}"
            for detail in error.errors()
        )
        message = f"Invalid Sources config: Source {source.id!r}: {problems}"
        logger.error(message)
        raise SourcesConfigError(message) from error


async def send_checked_request(
    http: HttpClient, source: SourceConfig, request: HttpRequest
) -> str:
    """Send a request and return its body if the status is 2xx.

    Args:
        http: Client the request goes through.
        source: The Source the request is for.
        request: The request.

    Returns:
        The response body.

    Raises:
        SourceFetchError: On a network error or a non-2xx status.
    """
    try:
        response = await http.send_request(request)
    except HttpRequestError as error:
        fail_source_fetch(source, f"request failed: {error}")
    if not 200 <= response.status_code < 300:
        fail_source_fetch(source, f"HTTP {response.status_code} from {request.url}")
    logger.debug("Source %s: %d characters from %s", source.id, len(response.text), request.url)
    return response.text


async def fetch_html_page(
    http: HttpClient, source: SourceConfig, request: HttpRequest
) -> BeautifulSoup:
    """Send a request and parse its body as HTML.

    Args:
        http: Client the request goes through.
        source: The Source the request is for.
        request: The request.

    Returns:
        The parsed page. Broken markup is parsed as well as it can be.

    Raises:
        SourceFetchError: On a network error or a non-2xx status.
    """
    text = await send_checked_request(http, source, request)
    return BeautifulSoup(text, "html.parser")


def read_list_items(
    source: SourceConfig, options: JsonItemsOptions, payload: object
) -> list[object]:
    """Read the items of one list page.

    Args:
        source: The Source the page comes from.
        options: Where the items are.
        payload: The parsed page.

    Returns:
        The page's items.

    Raises:
        SourceFetchError: If the items path does not hold a list.
    """
    items = read_json_path(payload, options.items_path)
    if not isinstance(items, list):
        fail_source_fetch(source, f"items path {options.items_path!r} does not hold a list")
    return items


def read_listed_vacancy(
    source: SourceConfig, options: JsonItemsOptions, item: object
) -> ListedVacancy:
    """Read one JSON list item into a listed Vacancy.

    Args:
        source: The Source the item comes from.
        options: Where the ID and fields are inside the item.
        item: One item of the list.

    Returns:
        The listed Vacancy.

    Raises:
        SourceFetchError: If the item has no ID, or its ID is blank.
    """
    vacancy_id = normalize_field_value(read_json_path(item, options.id_field))
    if vacancy_id is None:
        fail_source_fetch(source, f"list item has no ID at {options.id_field!r}")
    fields = {
        name: normalize_field_value(read_json_path(item, path))
        for name, path in options.fields.items()
    }
    return ListedVacancy(vacancy_id=vacancy_id, fields=fields)


def normalize_field_value(value: object) -> str | None:
    """Turn a read value into stripped text, with blanks as ``None``.

    Args:
        value: Value read from a list item, or ``None`` when it is missing.

    Returns:
        The text without surrounding whitespace, or ``None`` when the value is
        missing, empty or only whitespace.
    """
    text = "" if value is None else str(value).strip()
    return text or None


def read_json_path(data: object, path: str) -> object:
    """Follow a dotted path, such as ``data.items`` or ``tags.0``, into JSON.

    Args:
        data: Parsed JSON.
        path: Dotted path. An empty path returns ``data`` itself.

    Returns:
        The value at the path, or ``None`` when the path does not exist. A
        JSON ``null`` is treated the same as a missing value.
    """
    value = data
    for part in path.split(".") if path else []:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def format_query_params(params: QueryParams) -> dict[str, str]:
    """Turn configured query parameters into text.

    Args:
        params: Parameters from the config.

    Returns:
        The parameters with text values.
    """
    return {key: str(value) for key, value in params.items()}


def fill_vacancy_id(pattern: str, vacancy_id: str) -> str:
    """Put a Vacancy's ID into a pattern in place of ``{id}``.

    Args:
        pattern: Text that may contain ``{id}``.
        vacancy_id: The site's own ID for the Vacancy.

    Returns:
        The pattern with the ID filled in.
    """
    return pattern.replace("{id}", vacancy_id)


def fail_source_fetch(source: SourceConfig, problem: str) -> NoReturn:
    """Log and raise a fetch error for a Source.

    Args:
        source: The Source being read.
        problem: What went wrong.

    Raises:
        SourceFetchError: Always.
    """
    logger.error("Source %s: %s", source.id, problem)
    raise SourceFetchError(problem)
