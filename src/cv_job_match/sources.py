"""Loading and validation of the Sources config."""

import logging
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from pydantic_core import ErrorDetails

from cv_job_match.constants import DURATION_PATTERN, DURATION_UNITS, TECHNIQUE_OPTION_KEYS
from cv_job_match.exceptions import SourcesConfigError

logger = logging.getLogger(__name__)

Technique = Literal["json_endpoint", "html_css", "embedded_state"]


def parse_duration_text(value: object) -> timedelta:
    """Turn a duration such as ``12h`` or ``1d`` into a timedelta.

    Args:
        value: Raw value from the config file.

    Returns:
        The duration as a timedelta.

    Raises:
        ValueError: If the value is not text made of a positive whole number
            followed by one of the units s, m, h or d.
    """
    match = DURATION_PATTERN.match(value.strip()) if isinstance(value, str) else None
    if match is None or int(match.group(1)) == 0:
        logger.debug("Rejected duration %r", value)
        raise ValueError(f"invalid duration {value!r}, expected a value such as '12h' or '1d'")
    amount, unit = match.groups()
    return timedelta(**{DURATION_UNITS[unit]: int(amount)})


Duration = Annotated[timedelta, BeforeValidator(parse_duration_text)]


def require_text(value: str) -> str:
    """Strip surrounding whitespace and reject text that is left empty.

    Args:
        value: Text from the config file.

    Returns:
        The text without surrounding whitespace.

    Raises:
        ValueError: If the text is empty or only whitespace.
    """
    stripped = value.strip()
    if not stripped:
        logger.debug("Rejected blank text %r", value)
        raise ValueError("must not be blank")
    return stripped


RequiredText = Annotated[str, AfterValidator(require_text)]


class SourceDefaults(BaseModel):
    """Settings shared by every Source unless a Source overrides them.

    Attributes:
        interval: How often a Source is checked.
    """

    model_config = ConfigDict(frozen=True)

    interval: Duration = timedelta(days=1)


class SourceConfig(BaseModel):
    """One Source: a search or listing on a Company's careers site.

    Attributes:
        id: Unique identifier of the Source.
        company: Name of the Company the Source belongs to.
        technique: Name of the technique adapter that reads the Source.
        enabled: Whether the Source is checked.
        interval: Override of the default check interval, if any.
        notes: Free-text notes for the Candidate.
        options: Technique options: the Source's ``list``, ``detail`` and
            ``extra_ca`` keys. Their meaning belongs to the technique adapter.
    """

    model_config = ConfigDict(frozen=True)

    id: RequiredText
    company: RequiredText
    technique: Technique
    enabled: bool = True
    interval: Duration | None = None
    notes: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def collect_technique_options(cls, data: object) -> object:
        """Group the technique option keys of a Source under ``options``.

        Args:
            data: Raw mapping for one Source.

        Returns:
            The mapping with technique options grouped under ``options``.

        Raises:
            ValueError: If a key is neither a Source field nor a technique
                option, which is usually a typo.
        """
        if not isinstance(data, dict):
            return data
        field_names = cls.model_fields.keys() - {"options"}
        unknown_keys = sorted(data.keys() - field_names - TECHNIQUE_OPTION_KEYS, key=str)
        if unknown_keys:
            logger.debug("Rejected unknown Source keys %s", unknown_keys)
            raise ValueError(f"unknown key {', '.join(map(repr, unknown_keys))}")
        known = {key: value for key, value in data.items() if key in field_names}
        options = {key: value for key, value in data.items() if key in TECHNIQUE_OPTION_KEYS}
        return {**known, "options": options}


class SourcesConfig(BaseModel):
    """The whole Sources config.

    Attributes:
        defaults: Settings shared by every Source.
        sources: Every configured Source, in file order.
    """

    model_config = ConfigDict(frozen=True)

    defaults: SourceDefaults = SourceDefaults()
    sources: list[SourceConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def check_unique_source_ids(self) -> "SourcesConfig":
        """Reject two Sources with the same id.

        Returns:
            The config unchanged.

        Raises:
            ValueError: If an id is used by more than one Source.
        """
        seen: set[str] = set()
        for source in self.sources:
            if source.id in seen:
                logger.debug("Rejected duplicate Source id %r", source.id)
                raise ValueError(f"duplicate Source id {source.id!r}")
            seen.add(source.id)
        return self

    def resolve_interval(self, source: SourceConfig) -> timedelta:
        """Return how often a Source is checked.

        Args:
            source: A Source from this config.

        Returns:
            The Source's own interval, or the default one.
        """
        return source.interval or self.defaults.interval


def load_sources_config(path: Path) -> SourcesConfig:
    """Read and validate the Sources config file.

    Args:
        path: Path to the YAML file.

    Returns:
        The validated Sources config.

    Raises:
        SourcesConfigError: If the file cannot be read, is not valid YAML, is
            empty, or does not match the Sources config schema. The message
            names the Source and the problem.
    """
    logger.info("Loading Sources config from %s", path)
    data = read_yaml_mapping(path)
    try:
        config = SourcesConfig.model_validate(data)
    except ValidationError as error:
        fail_config_load(path, describe_validation_errors(error, data))
    logger.info("Loaded %d Sources from %s", len(config.sources), path)
    return config


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML file whose top level must be a mapping.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed top-level mapping.

    Raises:
        SourcesConfigError: If the file cannot be read or parsed, is empty, or
            its top level is not a mapping.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        fail_config_load(path, f"cannot read file ({error.strerror})")
    try:
        data = yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError as error:
        fail_config_load(path, f"malformed YAML ({error})")
    if data is None:
        fail_config_load(path, "file is empty")
    if not isinstance(data, dict):
        fail_config_load(path, "top level must be a mapping with a 'sources' list")
    logger.debug("Parsed Sources config keys: %s", list(data))
    return data


def describe_validation_errors(error: ValidationError, data: dict[str, Any]) -> str:
    """Turn pydantic errors into one message that names each Source.

    Args:
        error: The validation error raised for the config.
        data: The raw config the error was raised for.

    Returns:
        Every problem, separated by semicolons.
    """
    problems = []
    for detail in error.errors():
        problem = describe_error_detail(detail)
        if isinstance(detail.get("input"), str | int | float | bool):
            problem = f"{problem} (got {detail['input']!r})"
        place = name_error_location(detail["loc"], data)
        problems.append(f"{place}: {problem}" if place else problem)
    return "; ".join(problems)


def describe_error_detail(detail: ErrorDetails) -> str:
    """Describe one pydantic error without its type prefix.

    Args:
        detail: One entry of ``ValidationError.errors()``.

    Returns:
        The message of a raised ``ValueError`` as is, or pydantic's own message.
    """
    return str(detail["ctx"]["error"]) if detail["type"] == "value_error" else detail["msg"]


def name_error_location(location: tuple[int | str, ...], data: dict[str, Any]) -> str:
    """Name the place in the config that an error points at.

    Args:
        location: Pydantic error location, such as ``("sources", 2, "company")``.
        data: The raw config the location refers to.

    Returns:
        A readable place, such as ``Source 'alpha-search': company``, or an
        empty string for the config as a whole.
    """
    if len(location) < 2 or location[0] != "sources" or not isinstance(location[1], int):
        return join_location_parts(location)
    index = location[1]
    raw_source = data["sources"][index]
    source_id = raw_source.get("id") if isinstance(raw_source, dict) else None
    label = f"Source {source_id!r}" if isinstance(source_id, str) else f"Source #{index + 1}"
    field = join_location_parts(location[2:])
    return f"{label}: {field}" if field else label


def join_location_parts(parts: tuple[int | str, ...]) -> str:
    """Join pydantic location parts into a dotted path.

    Args:
        parts: Location parts, such as ``("list", "url")``.

    Returns:
        The dotted path, such as ``list.url``.
    """
    return ".".join(str(part) for part in parts)


def fail_config_load(path: Path, problem: str) -> NoReturn:
    """Log and raise a Sources config error.

    Args:
        path: Path to the Sources config file.
        problem: What is wrong with it.

    Raises:
        SourcesConfigError: Always.
    """
    logger.error("Invalid Sources config %s: %s", path, problem)
    raise SourcesConfigError(f"Invalid Sources config {path}: {problem}")
