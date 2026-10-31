"""Application settings read from environment variables."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BeforeValidator, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_log_level(value: object) -> object:
    """Upper-case a log level name and strip surrounding whitespace.

    Args:
        value: Raw log level from the environment.

    Returns:
        The normalized name for text, or the value unchanged for other types
        so that pydantic reports them.
    """
    return value.strip().upper() if isinstance(value, str) else value


LogLevel = Annotated[
    Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], BeforeValidator(normalize_log_level)
]


class Settings(BaseSettings):
    """Settings for one run of the service.

    Values come from environment variables prefixed with ``CV_JOB_MATCH_``
    or from a ``.env`` file in the working directory.

    Attributes:
        sources_path: Path to the git-ignored Sources config file.
        profile_path: Path to the git-ignored Profile folder.
        log_level: Lowest level of the records written to the log file.
        extra_ca_path: PEM file with the extra root certificate trusted for
            the hosts of Sources with ``extra_ca: true``.
        llm_base_url: Base URL of the OpenAI-compatible AI provider, or
            ``None`` for the OpenAI API itself.
        llm_model: Chat model that makes Assessments.
        llm_api_key: API key of the AI provider.
    """

    model_config = SettingsConfigDict(env_prefix="CV_JOB_MATCH_", env_file=".env", extra="ignore")

    sources_path: Path = Path("sources.yaml")
    profile_path: Path = Path("profile")
    log_level: LogLevel = "DEBUG"
    extra_ca_path: Path | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
