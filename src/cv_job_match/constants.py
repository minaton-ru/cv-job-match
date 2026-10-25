"""Constants shared across the service."""

import re

DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
TECHNIQUE_OPTION_KEYS = frozenset({"list", "detail", "extra_ca"})

USAGE_ERROR_EXIT_CODE = 2
CONFIG_ERROR_EXIT_CODE = 3

PACKAGE_LOGGER_NAME = "cv_job_match"
LOG_FILE_NAME = "cv_job_match.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
