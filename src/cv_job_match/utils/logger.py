"""Logging setup that sends every log of the service to a rotating file.

Modules do not configure logging themselves. Each one creates its logger with
``logging.getLogger(__name__)``, and its records reach the file through the
package logger configured here.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from cv_job_match.constants import (
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    LOG_FORMAT,
    LOG_MAX_BYTES,
    PACKAGE_LOGGER_NAME,
)

logger = logging.getLogger(__name__)


def configure_file_logging(
    log_level: str, log_path: Path = Path(LOG_FILE_NAME)
) -> RotatingFileHandler:
    """Send the logs of the service, from ``log_level`` up, to a rotating log file.

    The file is rotated when it reaches 10 MB, keeping a few older files.
    Calling this again replaces the previous file handler instead of adding a
    second one.

    Args:
        log_level: Lowest level written, such as ``DEBUG`` or ``INFO``.
        log_path: Path to the log file.

    Returns:
        The file handler attached to the package logger.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    for old_handler in list(package_logger.handlers):
        if isinstance(old_handler, RotatingFileHandler):
            package_logger.removeHandler(old_handler)
            old_handler.close()
    file_handler = RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    package_logger.addHandler(file_handler)
    package_logger.setLevel(log_level)
    logger.info("Logging to %s at level %s", log_path, log_level)
    return file_handler
