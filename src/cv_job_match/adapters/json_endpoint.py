"""Technique adapter for Sources whose list comes from a JSON endpoint."""

import json
import logging
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cv_job_match.discovery import ListedVacancy
from cv_job_match.exceptions import HttpRequestError, SourceFetchError, SourcesConfigError
from cv_job_match.http_client import HttpClient, HttpRequest
from cv_job_match.sources import RequiredText, SourceConfig, join_location_parts

logger = logging.getLogger(__name__)

QueryParams = dict[str, str | int | float]


class JsonListOptions(BaseModel):
    """How to fetch and read a Source's list.

    Attributes:
        url: Endpoint URL.
        method: HTTP method. Only GET is supported for now.
        params: Query parameters.
        items_path: Dotted path to the list of items, empty for the top level.
        id_field: Dotted path, inside an item, to the site's own ID.
        fields: Field name to dotted path inside an item, such as ``title``.
        pagination: Pagination settings. Not supported yet: only the first
            page is fetched.
    """

    model_config = ConfigDict(frozen=True)

    url: RequiredText
    method: Literal["GET"] = "GET"
    params: QueryParams = Field(default_factory=dict)
    items_path: str
    id_field: RequiredText
    fields: dict[str, RequiredText] = Field(default_factory=dict)
    pagination: object = None


class JsonDetailOptions(BaseModel):
    """How to fetch a Vacancy's description.

    Attributes:
        url: URL pattern; ``{id}`` is replaced with the Vacancy's ID.
        method: HTTP method. Only GET is supported for now.
        params: Query parameters; ``{id}`` in a value is replaced too.
        description_fields: Dotted paths to the parts of the description.
    """

    model_config = ConfigDict(frozen=True)

    url: RequiredText
    method: Literal["GET"] = "GET"
    params: QueryParams = Field(default_factory=dict)
    description_fields: list[RequiredText] = Field(min_length=1)


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
        """Fetch the first page of a Source's list.

        Args:
            source: The Source to read.

        Returns:
            Every listed Vacancy, in list order.

        Raises:
            SourceFetchError: If the list cannot be fetched or read, or an item
                has no ID.
            SourcesConfigError: If the Source's options are invalid.
        """
        options = parse_json_endpoint_options(source).list_options
        if options.pagination not in (None, "none"):
            logger.warning("Source %s: pagination is not supported yet, reading page 1", source.id)
        request = HttpRequest(url=options.url, params=format_query_params(options.params))
        payload = await self.fetch_json(source, request)
        items = read_json_path(payload, options.items_path)
        if not isinstance(items, list):
            fail_source_fetch(source, f"items path {options.items_path!r} does not hold a list")
        logger.debug("Source %s: list holds %d items", source.id, len(items))
        return [read_listed_vacancy(source, options, item) for item in items]

    async def fetch_vacancy_description(
        self, source: SourceConfig, vacancy: ListedVacancy
    ) -> str | None:
        """Fetch a Vacancy's description from the Source's detail URL pattern.

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
            params={
                key: fill_vacancy_id(value, vacancy.vacancy_id)
                for key, value in format_query_params(options.params).items()
            },
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
            f"{join_location_parts(detail['loc'])}: {detail['msg']}" for detail in error.errors()
        )
        message = f"Invalid Sources config: Source {source.id!r}: {problems}"
        logger.error(message)
        raise SourcesConfigError(message) from error


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
