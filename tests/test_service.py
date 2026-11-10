import asyncio

import pytest

from cv_job_match.exceptions import MissingSecretError
from cv_job_match.service import check_required_secrets, run_scheduler
from cv_job_match.settings import Settings
from tests.fakes import FakeSleep


def test_check_required_secrets_names_every_missing_secret() -> None:
    settings = Settings(llm_model="some-model", llm_api_key="  ", _env_file=None)

    with pytest.raises(
        MissingSecretError,
        match=(
            "CV_JOB_MATCH_TELEGRAM_BOT_TOKEN, CV_JOB_MATCH_TELEGRAM_CHAT_ID, "
            "CV_JOB_MATCH_LLM_API_KEY, CV_JOB_MATCH_DATABASE_URL$"
        ),
    ):
        check_required_secrets(settings)


async def test_scheduler_keeps_running_after_failed_tick() -> None:
    ticks: list[int] = []

    async def run_failing_first_tick() -> None:
        ticks.append(len(ticks))
        if len(ticks) == 1:
            raise ConnectionError("database is down")

    sleep = FakeSleep(stop_after=2)

    with pytest.raises(asyncio.CancelledError):
        await run_scheduler(run_failing_first_tick, 3600, sleep)

    assert ticks == [0, 1]
    assert sleep.delays == [3600, 3600]
