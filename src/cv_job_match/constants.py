"""Constants shared across the service."""

import re

DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
TECHNIQUE_OPTION_KEYS = frozenset({"list", "detail", "extra_ca"})

SUSPECT_RUN_EXIT_CODE = 1
USAGE_ERROR_EXIT_CODE = 2
CONFIG_ERROR_EXIT_CODE = 3

PACKAGE_LOGGER_NAME = "cv_job_match"
LOG_FILE_NAME = "cv_job_match.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

MIN_REQUEST_INTERVAL_S = 1.0
HTTP_TIMEOUT_S = 30.0
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

DEFAULT_MAX_PAGES = 100

PREVIEW_COUNT = 3
DESCRIPTION_PREVIEW_CHARS = 200
DESCRIPTION_FIELD = "description"

RESUME_FILE_NAME = "resume.md"
CRITERIA_FILE_NAME = "criteria.yaml"
DEFAULT_SCORE_THRESHOLD = 60

ASSESSMENT_INSTRUCTIONS = """\
You assess how well one job Vacancy fits the Candidate described below.
Return a score from 0 to 100, a verdict (apply, maybe or skip), the Candidate's strengths
that match the Vacancy, the gaps, and every exclusion the Vacancy triggers. Exclusions are
excluded Roles and anything the Candidate's preferences rule out, including ones that need
judgement rather than an exact keyword match. Keep every item short.

# Master resume

{resume}

# Criteria

Preferences: {preferences}
Excluded Roles: {excluded_roles}
Strengths: {strengths}
Weaknesses: {weaknesses}
"""
