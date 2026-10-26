"""The HTTP client seam and its polite production implementation."""

import asyncio
import logging
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from cv_job_match.constants import BROWSER_USER_AGENT, HTTP_TIMEOUT_S, MIN_REQUEST_INTERVAL_S
from cv_job_match.exceptions import HttpClientSetupError, HttpRequestError
from cv_job_match.settings import Settings
from cv_job_match.sources import SourcesConfig

logger = logging.getLogger(__name__)

TransportFactory = Callable[[ssl.SSLContext], httpx.AsyncBaseTransport]


@dataclass(frozen=True)
class HttpRequest:
    """One request to a careers site.

    Attributes:
        url: Absolute URL, without the query parameters.
        method: HTTP method.
        params: Query parameters.
    """

    url: str
    method: str = "GET"
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpResponse:
    """The answer to an :class:`HttpRequest`.

    Attributes:
        status_code: HTTP status code. Non-2xx codes are returned, not raised.
        text: Decoded response body.
    """

    status_code: int
    text: str


class HttpClient(Protocol):
    """Sends requests to careers sites. Every request goes through it."""

    async def send_request(self, request: HttpRequest) -> HttpResponse:
        """Send one request.

        Args:
            request: The request to send.

        Returns:
            The response, whatever its status code.

        Raises:
            HttpRequestError: If no response was received.
        """
        ...


def compute_request_delay(last_request_at: float | None, now: float, min_interval: float) -> float:
    """Return how long to wait before the next request to a host.

    Args:
        last_request_at: Monotonic time of the previous request to the host,
            or ``None`` when there was none.
        now: Current monotonic time.
        min_interval: Shortest allowed gap between two requests, in seconds.

    Returns:
        Seconds to wait, from 0 up to ``min_interval``.
    """
    if last_request_at is None:
        return 0.0
    return min(min_interval, max(0.0, last_request_at + min_interval - now))


def build_ssl_context(extra_ca_path: Path | None) -> ssl.SSLContext:
    """Build a verifying TLS context, optionally trusting one extra root certificate.

    Certificate and host name checks always stay on. The extra root
    certificate is added on top of the default CA bundle.

    Args:
        extra_ca_path: PEM file with the extra root certificate, or ``None``.

    Returns:
        The TLS context.

    Raises:
        HttpClientSetupError: If the extra root certificate cannot be loaded.
    """
    context = httpx.create_ssl_context()
    if extra_ca_path is None:
        return context
    try:
        context.load_verify_locations(cafile=extra_ca_path)
    except (OSError, ssl.SSLError) as error:
        message = f"cannot load extra root certificate {extra_ca_path}: {error}"
        logger.error("HTTP client setup failed: %s", message)
        raise HttpClientSetupError(message) from error
    logger.debug("Loaded extra root certificate %s", extra_ca_path)
    return context


def collect_extra_ca_hosts(sources: SourcesConfig) -> frozenset[str]:
    """Collect the hosts that need the extra root certificate.

    These are the hosts of the ``list`` and ``detail`` URLs of every Source
    whose ``extra_ca`` option is true.

    Args:
        sources: The Sources config.

    Returns:
        Host names, without ports.
    """
    hosts = set()
    for source in sources.sources:
        if source.options.get("extra_ca") is not True:
            continue
        for section in ("list", "detail"):
            options = source.options.get(section)
            url = options.get("url") if isinstance(options, dict) else None
            host = urlsplit(url).hostname if isinstance(url, str) else None
            if host:
                hosts.add(host)
    logger.debug("Hosts that need the extra root certificate: %s", sorted(hosts))
    return frozenset(hosts)


def create_default_transport(context: ssl.SSLContext) -> httpx.AsyncBaseTransport:
    """Build the real httpx transport for a TLS context.

    Args:
        context: TLS context the transport verifies servers with.

    Returns:
        The transport.
    """
    return httpx.AsyncHTTPTransport(verify=context)


class PoliteHttpClient:
    """Production HTTP client that is gentle with careers sites.

    It waits so that each host gets at most one request per interval, sends a
    browser-like User-Agent, and trusts the extra root certificate only for
    the configured hosts. Certificate verification is never turned off.
    Redirects are not followed, so every host is reached only through its
    own TLS context and its own wait; a redirect comes back as a 3xx response.
    """

    def __init__(
        self,
        *,
        extra_ca_hosts: frozenset[str] = frozenset(),
        extra_ca_path: Path | None = None,
        min_interval: float = MIN_REQUEST_INTERVAL_S,
        create_transport: TransportFactory = create_default_transport,
        read_clock: Callable[[], float] = time.monotonic,
        wait_seconds: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create the client.

        Args:
            extra_ca_hosts: Hosts that need the extra root certificate.
            extra_ca_path: PEM file with the extra root certificate, if any.
            min_interval: Shortest gap between two requests to one host, in seconds.
            create_transport: Builds the httpx transport for a TLS context.
            read_clock: Returns the current monotonic time in seconds.
            wait_seconds: Sleeps for the given number of seconds.

        Raises:
            HttpClientSetupError: If the extra root certificate cannot be loaded.
        """
        self.extra_ca_hosts = extra_ca_hosts
        self.default_context = build_ssl_context(None)
        self.extra_ca_context = build_ssl_context(extra_ca_path) if extra_ca_path else None
        self.min_interval = min_interval
        self.create_transport = create_transport
        self.read_clock = read_clock
        self.wait_seconds = wait_seconds
        self.last_request_at: dict[str, float] = {}

    async def send_request(self, request: HttpRequest) -> HttpResponse:
        """Wait for the host's turn, then send one request.

        Args:
            request: The request to send.

        Returns:
            The response, whatever its status code.

        Raises:
            HttpRequestError: If no response was received.
        """
        host = urlsplit(request.url).hostname or ""
        await self.wait_for_host_turn(host)
        logger.debug("Sending %s %s params=%s", request.method, request.url, dict(request.params))
        transport = self.create_transport(self.choose_ssl_context(host))
        try:
            async with httpx.AsyncClient(
                transport=transport,
                headers={"User-Agent": BROWSER_USER_AGENT},
                timeout=HTTP_TIMEOUT_S,
            ) as client:
                response = await client.request(
                    request.method, request.url, params=dict(request.params)
                )
        except httpx.HTTPError as error:
            message = f"{request.method} {request.url} failed: {error!r}"
            logger.error("HTTP request error: %s", message)
            raise HttpRequestError(message) from error
        logger.debug("Received %d from %s", response.status_code, request.url)
        return HttpResponse(status_code=response.status_code, text=response.text)

    async def wait_for_host_turn(self, host: str) -> None:
        """Wait until a request to the host keeps to the minimum interval.

        The slot is reserved before waiting, so concurrent requests to one
        host are spread out too.

        Args:
            host: Host name of the next request.
        """
        now = self.read_clock()
        delay = compute_request_delay(self.last_request_at.get(host), now, self.min_interval)
        self.last_request_at[host] = now + delay
        if delay > 0:
            logger.debug("Waiting %.2fs before the next request to %s", delay, host)
            await self.wait_seconds(delay)

    def choose_ssl_context(self, host: str) -> ssl.SSLContext:
        """Pick the TLS context for a host.

        Args:
            host: Host name of the request.

        Returns:
            The context with the extra root certificate for configured hosts,
            otherwise the default one.
        """
        if self.extra_ca_context is not None and host in self.extra_ca_hosts:
            return self.extra_ca_context
        return self.default_context


def create_polite_client(settings: Settings, sources: SourcesConfig) -> PoliteHttpClient:
    """Build the production HTTP client for the configured Sources.

    When some Sources need the extra root certificate but no file is set,
    a warning is logged and those hosts use the default CA bundle.

    Args:
        settings: Settings with the optional extra root certificate path.
        sources: The Sources config, which names the hosts that need it.

    Returns:
        The client.

    Raises:
        HttpClientSetupError: If the extra root certificate cannot be loaded.
    """
    extra_ca_hosts = collect_extra_ca_hosts(sources)
    if extra_ca_hosts and settings.extra_ca_path is None:
        logger.warning(
            "Sources need an extra root certificate for %s, but CV_JOB_MATCH_EXTRA_CA_PATH "
            "is not set",
            ", ".join(sorted(extra_ca_hosts)),
        )
    return PoliteHttpClient(extra_ca_hosts=extra_ca_hosts, extra_ca_path=settings.extra_ca_path)
