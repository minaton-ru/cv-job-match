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
TITLE_FIELD = "title"
CITY_FIELD = "city"

RESUME_FILE_NAME = "resume.md"
CRITERIA_FILE_NAME = "criteria.yaml"
DEFAULT_SCORE_THRESHOLD = 60

ALLOWED_STATUS_CHANGES = {
    "new": frozenset({"shortlisted", "applied", "dismissed"}),
    "shortlisted": frozenset({"shortlisted", "applied", "dismissed"}),
}
RECENT_ASSESSED_LIMIT = 10
DECISION_BUTTON_LABELS = {
    "shortlisted": "Shortlist",
    "applied": "Mark applied",
    "dismissed": "Dismiss",
}

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

TAILORED_RESUME_PDF_NAME = "resume.pdf"
TAILORED_RESUME_MARKDOWN_NAME = "resume.md"
RESUME_PAPER_SIZE = "A4"
RESUME_PDF_CSS = """\
body { font-family: sans-serif; font-size: 11pt; line-height: 1.4; }
h1 { font-size: 20pt; margin-bottom: 4pt; }
h2 { font-size: 14pt; margin-top: 12pt; border-bottom: 1px solid #888888; }
h3 { font-size: 12pt; margin-top: 8pt; }
li { margin-bottom: 2pt; }
"""

TAILORING_INSTRUCTIONS = """\
You tailor the Candidate's master resume and write a cover letter for one job Vacancy.
Use only facts from the master resume below. You may select, reorder and reword them to fit
the Vacancy, but never add a fact, skill, employer, date or number the master resume does not
hold. Return the tailored resume as Markdown and the cover letter as plain text, both in the
language with the code {language}.

# Master resume

{resume}
"""

DB_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
MIGRATIONS_FOLDER_NAME = "migrations"
MIGRATION_TARGET = "head"
