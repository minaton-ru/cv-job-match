from pathlib import Path

from cv_job_match.constants import CRITERIA_FILE_NAME, RESUME_FILE_NAME

RESUME_MD = "# Sam Example\n\nBackend Engineer with five years of Python.\n"
CRITERIA_YAML = """
language: en
preferences: [Remote work]
excluded_roles: [Sales Manager]
strengths: [Python]
weaknesses: [Frontend]
prefilter:
  title_keywords: [python, backend]
  excluded_title_keywords: [senior director]
  cities: [Example City]
"""


def write_profile(
    directory: Path, resume: str | None = RESUME_MD, criteria: str | None = CRITERIA_YAML
) -> Path:
    """Write a synthetic Profile folder.

    Args:
        directory: The Profile folder to create.
        resume: Master resume Markdown, or ``None`` to leave the file out.
        criteria: Criteria YAML, or ``None`` to leave the file out.

    Returns:
        Path to the Profile folder.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if resume is not None:
        (directory / RESUME_FILE_NAME).write_text(resume, encoding="utf-8")
    if criteria is not None:
        (directory / CRITERIA_FILE_NAME).write_text(criteria, encoding="utf-8")
    return directory
