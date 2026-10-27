"""Technique adapter for Sources whose list comes from a JSON endpoint."""

import json
import logging
from typing import Literal, NoReturn, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    model_validator,
)

from cv_job_match.constants import DEFAULT_MAX_PAGES
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


class JsonRequestOptions(BaseModel):
    """How to send a request to a JSON endpoint.

    Attributes:
        url: Endpoint URL.
        method: HTTP method, GET or POST.
        params: Query parameters.
        body: Body sent as JSON, only with POST.
    """

    model_config = ConfigDict(frozen=True)

    url: RequiredText
    method: Literal["GET", "POST"] = "GET"
    params: QueryParams = Field(default_factory=dict)
    body: dict[str, object] | None = None

    @model_validator(mode="after")
    def check_body_method(self) -> Self:
        """Reject a body on a GET request.

        Returns:
            The options unchanged.

        Raises:
            ValueError: If a body is set and the method is not POST.
        """
        if self.body is not None and self.method != "POST":
            logger.debug("Rejected body %s with method %s", self.body, self.method)
            raise ValueError("body needs method POST")
        return self


class PaginationOptions(BaseModel):
    """How to walk through the pages of a list.

    Attributes:
        style: ``page`` sends the page number, ``offset`` the index of the
            page's first item.
        param: Query parameter (GET) or top-level body key (POST) that
            carries the page number or offset.
        start: First page number or offset; 1 for ``page`` and 0 for
            ``offset`` when not set.
        size: Items per full page. A shorter page is the last one. Required
            for ``offset``.
        total_path: Dotted path, in each page, to the total number of items.
        max_pages: Most pages read; a longer list makes the fetch fail.
    """

    model_config = ConfigDict(frozen=True)

    style: Literal["page", "offset"]
    param: RequiredText
    start: int | None = None
    size: PositiveInt | None = None
    total_path: RequiredText | None = None
    max_pages: PositiveInt = DEFAULT_MAX_PAGES

    @model_validator(mode="after")
    def check_offset_size(self) -> Self:
        """Reject offset pagination without a page size.

        Returns:
            The options unchanged.

        Raises:
            ValueError: If the style is ``offset`` and ``size`` is not set.
        """
        if self.style == "offset" and self.size is None:
            logger.debug("Rejected offset pagination without size")
            raise ValueError("offset pagination needs size")
        return self

    def compute_page_value(self, page_index: int) -> int:
        """Return the page number or offset to send for a page.

        Args:
            page_index: Zero-based index of the page.

        Returns:
            The value of the pagination parameter.
        """
        if self.style == "offset" and self.size is not None:
            return (self.start or 0) + page_index * self.size
        return (1 if self.start is None else self.start) + page_index


class JsonListOptions(JsonRequestOptions):
    """How to fetch and read a Source's list.

    Attributes:
        items_path: Dotted path to the list of items, empty for the top level.
        id_field: Dotted path, inside an item, to the site's own ID.
        fields: Field name to dotted path inside an item, such as ``title``.
        pagination: Pagination settings, or ``None`` for a single page.
    """

    items_path: str
    id_field: RequiredText
    fields: dict[str, RequiredText] = Field(default_factory=dict)
    pagination: PaginationOptions | None = None

    @model_validator(mode="after")
    def check_pagination_param(self) -> Self:
        """Reject a pagination parameter that the request already sets.

        Returns:
            The options unchanged.

        Raises:
            ValueError: If the pagination parameter is also in ``params`` for
                GET, or in ``body`` for POST.
        """
        if self.pagination is None:
            return self
        place, taken = (
            ("body", self.body or {}) if self.method == "POST" else ("params", self.params)
        )
        if self.pagination.param in taken:
            logger.debug("Rejected pagination param %r set in %s", self.pagination.param, place)
            raise ValueError(f"pagination param {self.pagination.param!r} is also set in {place}")
        return self


class JsonDetailOptions(JsonRequestOptions):
    """How to fetch a Vacancy's description.

    ``{id}`` in the URL, in a query parameter value or in a body string is
    replaced with the Vacancy's ID.

    Attributes:
        description_fields: Dotted paths to the parts of the description.
    """

    description_fields: list[RequiredText] = Field(min_length=1)

    @model_validator(mode="after")
    def check_vacancy_id_placeholder(self) -> Self:
        """Reject a detail request that does not depend on the Vacancy.

        Returns:
            The options unchanged.

        Raises:
            ValueError: If ``{id}`` is in none of the URL, params values and
                body values, so filling in an ID would change nothing.
        """
        request_parts = [self.url, self.params, self.body]
        if fill_vacancy_id_in_json(request_parts, "") == request_parts:
            logger.debug("Rejected detail options without {id}: %s", self.url)
            raise ValueError("{id} must appear in url, params or body")
        return self


class JsonEndpointOptions(BaseModel):
    """Technique options of a ``json_endpoint`` Source.

    Attributes:
        list_options: How to fetch and read the list.
        detail_options: How to fetch descriptions, if configured.
    """

    model_config = ConfigDict(frozen=True)

    list_options: JsonListOptions = Field(alias="list")
    detail_options: JsonDetailOptions | None = Field(default=None, alias="detail")


class JsonEndpointAdapter:
    """Reads Sources whose list and descriptions come from JSON endpoints."""

    def __init__(self, http: HttpClient) -> None:
        """Create the adapter.

        Args:
            http: Client every request goes through.
        """
        self.http = http

    async def fetch_listed_vacancies(self, source: SourceConfig) -> list[ListedVacancy]:
        """Fetch every page of a Source's list.

        Pages are read one after another until the last one. A failure on any
        page fails the whole fetch, so a partial list is never returned. When
        ``max_pages`` full pages are read, one more page is fetched to check
        that it is empty.

        Args:
            source: The Source to read.

        Returns:
            Every listed Vacancy, in list order.

        Raises:
            SourceFetchError: If a page cannot be fetched or read, an item has
                no ID, the total is not a number, or the list has more than
                ``max_pages`` pages.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_json_endpoint_options(source).list_options
        pagination = options.pagination
        page_limit = pagination.max_pages if pagination else 1
        vacancies: list[ListedVacancy] = []
        page_index = 0
        while True:
            payload = await self.fetch_json(source, build_list_request(options, page_index))
            items = read_list_items(source, options, payload)
            logger.debug("Source %s: page %d holds %d items", source.id, page_index, len(items))
            if page_index == page_limit and items:
                fail_source_fetch(source, f"list has more than {page_limit} pages")
            vacancies.extend(read_listed_vacancy(source, options, item) for item in items)
            if pagination is None or is_last_page(
                source, pagination, payload, len(items), len(vacancies)
            ):
                return vacancies
            page_index += 1

    async def fetch_vacancy_description(
        self, source: SourceConfig, vacancy: ListedVacancy
    ) -> str | None:
        """Fetch a Vacancy's description from the Source's detail endpoint.

        Args:
            source: The Source that lists the Vacancy.
            vacancy: The listed Vacancy.

        Returns:
            The description parts joined by blank lines, or ``None`` when the
            Source has no ``detail`` options.

        Raises:
            SourceFetchError: If the description cannot be fetched, or none of
                its fields is present.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_json_endpoint_options(source).detail_options
        if options is None:
            return None
        request = HttpRequest(
            url=fill_vacancy_id(options.url, vacancy.vacancy_id),
            method=options.method,
            params={
                key: fill_vacancy_id(value, vacancy.vacancy_id)
                for key, value in format_query_params(options.params).items()
            },
            body=fill_vacancy_id_in_json(options.body, vacancy.vacancy_id),
        )
        payload = await self.fetch_json(source, request)
        parts = [read_json_path(payload, path) for path in options.description_fields]
        texts = [str(part) for part in parts if part is not None]
        if not texts:
            fail_source_fetch(
                source, f"no description fields found for Vacancy {vacancy.vacancy_id}"
            )
        return "\n\n".join(texts)

    async def fetch_json(self, source: SourceConfig, request: HttpRequest) -> object:
        """Send a request and parse its JSON body.

        Args:
            source: The Source the request is for.
            request: The request.

        Returns:
            The parsed body.

        Raises:
            SourceFetchError: On a network error, a non-2xx status or a body
                that is not JSON.
        """
        try:
            response = await self.http.send_request(request)
        except HttpRequestError as error:
            fail_source_fetch(source, f"request failed: {error}")
        if not 200 <= response.status_code < 300:
            fail_source_fetch(source, f"HTTP {response.status_code} from {request.url}")
        try:
            return json.loads(response.text)
        except ValueError:
            fail_source_fetch(source, f"malformed JSON from {request.url}")


def parse_json_endpoint_options(source: SourceConfig) -> JsonEndpointOptions:
    """Validate a Source's ``json_endpoint`` options.

    Args:
        source: The Source.

    Returns:
        The validated options.

    Raises:
        SourcesConfigError: If the options are invalid. The message names the
            Source and the problem.
    """
    try:
        return JsonEndpointOptions.model_validate(source.options)
    except ValidationError as error:
        problems = "; ".join(
            f"{join_location_parts(detail['loc'])}: {describe_error_detail(detail)}"
            for detail in error.errors()
        )
        message = f"Invalid Sources config: Source {source.id!r}: {problems}"
        logger.error(message)
        raise SourcesConfigError(message) from error


def build_list_request(options: JsonListOptions, page_index: int) -> HttpRequest:
    """Build the request for one page of a list.

    Args:
        options: The Source's list options.
        page_index: Zero-based index of the page; ignored without pagination.

    Returns:
        The request. The page number or offset goes into the body for POST
        and into the query parameters for GET.
    """
    params, body = format_query_params(options.params), options.body
    pagination = options.pagination
    if pagination is not None:
        value = pagination.compute_page_value(page_index)
        if options.method == "POST":
            body = {**(body or {}), pagination.param: value}
        else:
            params = {**params, pagination.param: str(value)}
    return HttpRequest(url=options.url, method=options.method, params=params, body=body)


def read_list_items(
    source: SourceConfig, options: JsonListOptions, payload: object
) -> list[object]:
    """Read the items of one list page.

    Args:
        source: The Source the page comes from.
        options: The Source's list options.
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


def is_last_page(
    source: SourceConfig,
    pagination: PaginationOptions,
    payload: object,
    page_item_count: int,
    read_count: int,
) -> bool:
    """Decide whether a page is the last one of a list.

    Args:
        source: The Source the page comes from.
        pagination: The Source's pagination options.
        payload: The parsed page.
        page_item_count: How many items the page holds.
        read_count: How many items all pages so far hold.

    Returns:
        ``True`` when the page is empty, shorter than ``size``, or brings the
        count up to the total.

    Raises:
        SourceFetchError: If ``total_path`` is set but does not hold a number.
    """
    if page_item_count == 0:
        return True
    if pagination.size is not None and page_item_count < pagination.size:
        return True
    if pagination.total_path is None:
        return False
    total = read_json_path(payload, pagination.total_path)
    if not isinstance(total, int) or isinstance(total, bool):
        fail_source_fetch(source, f"total path {pagination.total_path!r} does not hold a number")
    return read_count >= total


def read_listed_vacancy(
    source: SourceConfig, options: JsonListOptions, item: object
) -> ListedVacancy:
    """Read one list item into a listed Vacancy.

    Args:
        source: The Source the item comes from.
        options: The Source's list options.
        item: One item of the list.

    Returns:
        The listed Vacancy.

    Raises:
        SourceFetchError: If the item has no ID, or its ID is blank.
    """
    vacancy_id = read_json_path(item, options.id_field)
    if vacancy_id is None or not str(vacancy_id).strip():
        fail_source_fetch(source, f"list item has no ID at {options.id_field!r}")
    fields = {name: read_json_path(item, path) for name, path in options.fields.items()}
    return ListedVacancy(
        vacancy_id=str(vacancy_id).strip(),
        fields={name: None if value is None else str(value) for name, value in fields.items()},
    )


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


def fill_vacancy_id_in_json[JsonValue](value: JsonValue, vacancy_id: str) -> JsonValue:
    """Put a Vacancy's ID into every text inside a JSON value in place of ``{id}``.

    Args:
        value: Parsed JSON, such as a request body.
        vacancy_id: The site's own ID for the Vacancy.

    Returns:
        A copy of the value with the ID filled in. Values that are not text
        are kept as they are.
    """
    if isinstance(value, str):
        return fill_vacancy_id(value, vacancy_id)
    if isinstance(value, dict):
        return {key: fill_vacancy_id_in_json(item, vacancy_id) for key, item in value.items()}
    if isinstance(value, list):
        return [fill_vacancy_id_in_json(item, vacancy_id) for item in value]
    return value


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
