"""Alerts that tell the Candidate about Suspect Scrape Runs."""

import logging

from cv_job_match.discovery import DiscoveryReport, RunRecord
from cv_job_match.evaluation import Notifier

logger = logging.getLogger(__name__)


async def send_suspect_run_alerts(notifier: Notifier, report: DiscoveryReport) -> int:
    """Send one alert per Suspect Scrape Run, logging a failed send instead of raising it.

    Args:
        notifier: Sends messages to the Candidate.
        report: The Scrape Runs of one tick.

    Returns:
        How many alerts were sent.
    """
    sent_count = 0
    for run in report.runs:
        if run.status != "suspect":
            continue
        try:
            await notifier.send_message(format_suspect_run_alert(run))
        except Exception as error:
            logger.error(
                "Alert about suspect Source %s was not sent: %s",
                run.source_id,
                error,
                exc_info=True,
            )
            continue
        logger.info("Sent alert about suspect Source %s", run.source_id)
        sent_count += 1
    return sent_count


def format_suspect_run_alert(run: RunRecord) -> str:
    """Build the alert text for a Suspect Scrape Run.

    Args:
        run: The suspect run.

    Returns:
        The alert naming the Source, its Company and the reason.
    """
    return f"Suspect Scrape Run of Source {run.source_id} ({run.company}): {run.error}"
