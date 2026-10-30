"""Loading, validation and versioning of the Candidate Profile."""

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cv_job_match.constants import (
    CRITERIA_FILE_NAME,
    DEFAULT_SCORE_THRESHOLD,
    RESUME_FILE_NAME,
)
from cv_job_match.exceptions import ProfileError, RepositoryNotConfiguredError
from cv_job_match.sources import RequiredText

logger = logging.getLogger(__name__)

TextList = list[RequiredText]


class PrefilterRules(BaseModel):
    """Keyword rules of the Pre-filter.

    Attributes:
        title_keywords: Words a Vacancy title should contain.
        excluded_title_keywords: Words that reject a Vacancy title.
        cities: Cities a Vacancy may be in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title_keywords: TextList = Field(default_factory=list)
    excluded_title_keywords: TextList = Field(default_factory=list)
    cities: TextList = Field(default_factory=list)


class ProfileCriteria(BaseModel):
    """The Candidate's criteria from the criteria file.

    Attributes:
        language: The one language of Vacancies and Application Packs.
        score_threshold: Lowest Assessment score that reaches the Candidate.
        preferences: What the Candidate prefers in a job.
        excluded_roles: Roles the Candidate never wants.
        strengths: The Candidate's strengths.
        weaknesses: The Candidate's weaknesses.
        prefilter: Keyword rules of the Pre-filter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: RequiredText
    score_threshold: Annotated[int, Field(ge=0, le=100)] = DEFAULT_SCORE_THRESHOLD
    preferences: TextList = Field(default_factory=list)
    excluded_roles: TextList = Field(default_factory=list)
    strengths: TextList = Field(default_factory=list)
    weaknesses: TextList = Field(default_factory=list)
    prefilter: PrefilterRules = PrefilterRules()


@dataclass(frozen=True)
class ProfileSnapshot:
    """One version of the Candidate Profile.

    Attributes:
        resume: The master resume Markdown.
        criteria: The parsed criteria.
        version: Hash of the resume and criteria files; any edit changes it.
    """

    resume: str
    criteria: ProfileCriteria
    version: str


class ProfileRepository(Protocol):
    """Storage of Profile snapshots."""

    async def save_snapshot(self, snapshot: ProfileSnapshot) -> bool:
        """Save a snapshot unless its version is already stored.

        Args:
            snapshot: The Profile version to save.

        Returns:
            Whether the snapshot was saved, that is whether its version is new.
        """
        ...


class CandidateProfile:
    """The loaded Candidate Profile and the saving of its versions."""

    def __init__(self, snapshot: ProfileSnapshot, repository: ProfileRepository | None) -> None:
        """Hold a loaded Profile.

        Args:
            snapshot: The Profile loaded at startup.
            repository: Storage of Profile snapshots, if the app has one.
        """
        self.snapshot = snapshot
        self.repository = repository

    async def save_snapshot_if_new(self) -> bool:
        """Save the loaded Profile version unless it is already stored.

        Returns:
            Whether the version was new and is now saved.

        Raises:
            RepositoryNotConfiguredError: If the app has no Profile repository.
        """
        if self.repository is None:
            logger.error("Cannot save Profile snapshot: no Profile repository")
            raise RepositoryNotConfiguredError("Saving a Profile snapshot needs a repository")
        saved = await self.repository.save_snapshot(self.snapshot)
        logger.info("Profile version %s %s", self.snapshot.version, "saved" if saved else "known")
        return saved


def load_profile(path: Path) -> ProfileSnapshot:
    """Read and validate the Profile folder.

    Args:
        path: The Profile folder with the resume and criteria files.

    Returns:
        The resume, the parsed criteria and the version hash.

    Raises:
        ProfileError: If the folder or a file is missing or unreadable, the
            resume is empty, or the criteria are not valid YAML or do not
            match the criteria schema.
    """
    logger.info("Loading Profile from %s", path)
    if not path.is_dir():
        fail_profile_load(path, "folder not found")
    resume = read_profile_file(path / RESUME_FILE_NAME)
    if not resume.strip():
        fail_profile_load(path, f"{RESUME_FILE_NAME} is empty")
    criteria_text = read_profile_file(path / CRITERIA_FILE_NAME)
    criteria = parse_criteria_text(path, criteria_text)
    version = hash_profile_version(resume, criteria_text)
    logger.info("Loaded Profile version %s", version)
    return ProfileSnapshot(resume=resume, criteria=criteria, version=version)


def read_profile_file(path: Path) -> str:
    """Read one file of the Profile folder.

    Args:
        path: Path to the file.

    Returns:
        The file's text.

    Raises:
        ProfileError: If the file cannot be read or is not UTF-8 text.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        fail_profile_load(path.parent, f"cannot read {path.name} ({error.strerror})")
    except UnicodeDecodeError:
        fail_profile_load(path.parent, f"{path.name} is not UTF-8 text")


def parse_criteria_text(path: Path, text: str) -> ProfileCriteria:
    """Parse and validate the criteria YAML.

    Args:
        path: The Profile folder, for error messages.
        text: Text of the criteria file.

    Returns:
        The validated criteria.

    Raises:
        ProfileError: If the YAML is malformed or does not match the schema.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        fail_profile_load(path, f"{CRITERIA_FILE_NAME}: malformed YAML ({error})")
    logger.debug(
        "Parsed Profile criteria keys: %s", list(data) if isinstance(data, dict) else data
    )
    try:
        return ProfileCriteria.model_validate(data if data is not None else {})
    except ValidationError as error:
        fail_profile_load(path, f"{CRITERIA_FILE_NAME}: {describe_criteria_errors(error)}")


def describe_criteria_errors(error: ValidationError) -> str:
    """Turn pydantic errors into one message that names each field.

    Args:
        error: The validation error raised for the criteria.

    Returns:
        Every problem as ``field: message``, separated by semicolons.
    """
    return "; ".join(
        f"{'.'.join(map(str, detail['loc'])) or 'criteria'}: {detail['msg']}"
        for detail in error.errors()
    )


def hash_profile_version(resume: str, criteria_text: str) -> str:
    """Compute the version hash of a Profile.

    Args:
        resume: Text of the resume file.
        criteria_text: Text of the criteria file.

    Returns:
        A SHA-256 hex digest that changes with any edit of either file.
    """
    digest = hashlib.sha256()
    for text in (resume, criteria_text):
        encoded = text.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
    return digest.hexdigest()


def fail_profile_load(path: Path, problem: str) -> NoReturn:
    """Log and raise a Profile error.

    Args:
        path: The Profile folder.
        problem: What is wrong with it.

    Raises:
        ProfileError: Always.
    """
    logger.error("Invalid Profile %s: %s", path, problem)
    raise ProfileError(f"Invalid Profile {path}: {problem}")
