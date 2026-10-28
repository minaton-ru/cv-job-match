import asyncio
import json
import ssl
from collections.abc import Mapping
from datetime import datetime

import httpx

from cv_job_match.discovery import NewVacancy, RunRecord, SourceState
from cv_job_match.exceptions import HttpRequestError
from cv_job_match.http_client import HttpRequest, HttpResponse


class FakeClock:
    """Clock that always returns the time it was given."""

    def __init__(self, now: datetime) -> None:
        """Create the clock.

        Args:
            now: The time every call returns.
        """
        self.now = now

    def now_utc(self) -> datetime:
        """Return the fixed time.

        Returns:
            The time the clock was created with.
        """
        return self.now


class FakeDependency:
    """Stand-in for a dependency whose interface a later ticket defines."""

    def __init__(self, name: str) -> None:
        """Create the stand-in.

        Args:
            name: Which dependency it stands in for.
        """
        self.name = name


class FakeDiscoveryRepository:
    """In-memory Discovery storage that saves each Scrape Run all at once.

    Loading and saving yield to the event loop, as real database calls do, so
    runs that should not overlap are caught when they do.
    """

    def __init__(self) -> None:
        """Create an empty repository."""
        self.runs: list[RunRecord] = []
        self.vacancies: dict[tuple[str, str], NewVacancy] = {}
        self.save_error: Exception | None = None

    async def load_state(self, source_id: str, company: str) -> SourceState:
        """Return what is known before a Source is checked.

        Args:
            source_id: The Source to check.
            company: The Source's Company.

        Returns:
            The Source's last successful run and the Company's known Vacancy IDs.
        """
        await asyncio.sleep(0)
        successful = [
            run for run in self.runs if run.source_id == source_id and run.status != "suspect"
        ]
        known_ids = frozenset(
            vacancy_id for owner, vacancy_id in self.vacancies if owner == company
        )
        return SourceState(source_id, company, successful[-1] if successful else None, known_ids)

    async def load_last_runs(self) -> dict[str, datetime]:
        """Return when each Source last ran.

        Returns:
            Start time of the latest run of any status, by Source id.
        """
        return {run.source_id: run.started_at for run in self.runs}

    async def save_run(self, record: RunRecord) -> None:
        """Save a run and its new Vacancies, or nothing when ``save_error`` is set.

        Args:
            record: The run to save.

        Raises:
            Exception: The ``save_error``, when one is set.
        """
        await asyncio.sleep(0)
        if self.save_error is not None:
            raise self.save_error
        self.runs.append(record)
        for vacancy in record.new_vacancies:
            self.vacancies[(record.company, vacancy.vacancy_id)] = vacancy


class FakeTransportFactory:
    """Builds fake httpx transports and logs every request they receive."""

    def __init__(self, error: Exception | None = None, redirect_to: str | None = None) -> None:
        """Create the factory.

        Args:
            error: Error every transport raises instead of answering, if any.
            redirect_to: URL every transport redirects to instead of answering, if any.
        """
        self.error = error
        self.redirect_to = redirect_to
        self.sent: list[tuple[httpx.Request, ssl.SSLContext]] = []

    def create_transport(self, context: ssl.SSLContext) -> "FakeTransport":
        """Build a transport bound to a TLS context.

        Args:
            context: TLS context the real transport would verify with.

        Returns:
            A fake transport that logs into this factory.
        """
        return FakeTransport(self, context)


class FakeTransport(httpx.AsyncBaseTransport):
    """httpx transport that answers every request with ``200 ok`` or a redirect."""

    def __init__(self, factory: FakeTransportFactory, context: ssl.SSLContext) -> None:
        """Create the transport.

        Args:
            factory: Factory that keeps the request log.
            context: TLS context the transport was built with.
        """
        self.factory = factory
        self.context = context

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Log the request and answer it.

        Args:
            request: The outgoing request.

        Returns:
            A ``302`` to the factory's redirect URL, or a ``200`` with the body ``ok``.

        Raises:
            Exception: The factory's error, when it has one.
        """
        self.factory.sent.append((request, self.context))
        if self.factory.error is not None:
            raise self.factory.error
        if self.factory.redirect_to is not None:
            return httpx.Response(
                302, headers={"Location": self.factory.redirect_to}, request=request
            )
        return httpx.Response(200, text="ok", request=request)


class FakeTimer:
    """Monotonic clock and sleep that advance only when waited on."""

    def __init__(self, now: float = 100.0) -> None:
        """Create the timer.

        Args:
            now: Starting monotonic time.
        """
        self.now = now
        self.waits: list[float] = []

    def read_clock(self) -> float:
        """Return the current fake monotonic time.

        Returns:
            The fake time in seconds.
        """
        return self.now

    async def wait_seconds(self, seconds: float) -> None:
        """Record a wait and move the clock forward by it.

        Args:
            seconds: How long the caller waits.
        """
        self.waits.append(seconds)
        self.now += seconds


CannedAnswer = HttpResponse | Exception


class FakeHttpClient:
    """HTTP client with canned responses or errors per URL and a request log."""

    def __init__(self, responses: Mapping[str, CannedAnswer | list[CannedAnswer]]) -> None:
        """Create the client.

        Args:
            responses: What each URL answers with: a response, or an error to
                raise. A list answers successive requests to the URL in order.
        """
        self.responses = {
            url: list(answer) if isinstance(answer, list) else answer
            for url, answer in responses.items()
        }
        self.requests: list[HttpRequest] = []

    async def send_request(self, request: HttpRequest) -> HttpResponse:
        """Log the request and return its canned answer.

        Args:
            request: The request to send.

        Returns:
            The canned response for the request's URL.

        Raises:
            HttpRequestError: If the URL has no canned answer left.
            Exception: The canned error for the URL.
        """
        self.requests.append(request)
        answer = self.responses.get(request.url)
        if isinstance(answer, list):
            answer = answer.pop(0) if answer else None
        if answer is None:
            raise HttpRequestError(f"no canned response for {request.url}")
        if isinstance(answer, Exception):
            raise answer
        return answer


def build_json_response(payload: object, status_code: int = 200) -> HttpResponse:
    """Build a canned JSON response.

    Args:
        payload: Value to serialize as the body.
        status_code: HTTP status code.

    Returns:
        The response.
    """
    return HttpResponse(status_code=status_code, text=json.dumps(payload))


def build_html_response(body: str, status_code: int = 200) -> HttpResponse:
    """Build a canned HTML response.

    Args:
        body: Markup inside ``<body>``.
        status_code: HTTP status code.

    Returns:
        The response.
    """
    return HttpResponse(status_code=status_code, text=f"<html><body>{body}</body></html>")
