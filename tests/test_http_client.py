import ssl
from pathlib import Path

import httpx
import pytest

from cv_job_match.exceptions import HttpClientSetupError, HttpRequestError
from cv_job_match.http_client import (
    HttpRequest,
    PoliteHttpClient,
    build_ssl_context,
    collect_extra_ca_hosts,
    compute_request_delay,
    create_polite_client,
)
from cv_job_match.settings import Settings
from cv_job_match.sources import SourcesConfig
from tests.fakes import FakeTimer, FakeTransportFactory

SYNTHETIC_ROOT_CA = Path(__file__).parent / "fixtures" / "synthetic_root_ca.pem"


@pytest.mark.parametrize(
    ("last_request_at", "now", "expected_delay"),
    [
        (None, 100.0, 0.0),
        (100.0, 100.0, 1.0),
        (100.0, 100.25, 0.75),
        (100.0, 101.0, 0.0),
        (100.0, 105.0, 0.0),
        (100.0, 99.5, 1.0),
    ],
    ids=[
        "first-request",
        "same-instant",
        "inside-interval",
        "at-interval",
        "after-interval",
        "clock-went-back",
    ],
)
def test_compute_request_delay(
    last_request_at: float | None, now: float, expected_delay: float
) -> None:
    assert compute_request_delay(last_request_at, now, 1.0) == pytest.approx(expected_delay)


@pytest.mark.parametrize(
    ("extra_ca_path", "has_extra_root"),
    [(None, False), (SYNTHETIC_ROOT_CA, True)],
    ids=["default-bundle", "with-extra-root"],
)
def test_build_ssl_context(extra_ca_path: Path | None, has_extra_root: bool) -> None:
    context = build_ssl_context(extra_ca_path)

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert has_synthetic_root(context) is has_extra_root
    assert len(context.get_ca_certs()) > 1


def test_build_ssl_context_rejects_missing_ca_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(HttpClientSetupError, match="cannot load extra root certificate"):
        build_ssl_context(tmp_path / "absent.pem")

    assert "cannot load extra root certificate" in caplog.text


def test_collect_extra_ca_hosts_includes_only_configured_sources() -> None:
    config = SourcesConfig.model_validate(
        {
            "sources": [
                {
                    "id": "rho-jobs",
                    "company": "Rho",
                    "technique": "json_endpoint",
                    "extra_ca": True,
                    "list": {"url": "https://api.rho.example/jobs"},
                    "detail": {"url": "https://jobs.rho.example/view/{id}"},
                },
                {
                    "id": "sigma-jobs",
                    "company": "Sigma",
                    "technique": "json_endpoint",
                    "extra_ca": False,
                    "list": {"url": "https://sigma.example/jobs"},
                },
                {
                    "id": "upsilon-jobs",
                    "company": "Upsilon",
                    "technique": "json_endpoint",
                    "extra_ca": True,
                    "list": "not-a-mapping",
                },
                {
                    "id": "tau-jobs",
                    "company": "Tau",
                    "technique": "json_endpoint",
                    "list": {"url": "https://tau.example/jobs"},
                },
            ]
        }
    )

    assert collect_extra_ca_hosts(config) == {"api.rho.example", "jobs.rho.example"}


def build_polite_client(
    transports: FakeTransportFactory, timer: FakeTimer, **options: object
) -> PoliteHttpClient:
    return PoliteHttpClient(
        create_transport=transports.create_transport,
        read_clock=timer.read_clock,
        wait_seconds=timer.wait_seconds,
        **options,
    )


async def test_polite_client_sends_browser_user_agent() -> None:
    transports = FakeTransportFactory()
    client = build_polite_client(transports, FakeTimer())

    response = await client.send_request(
        HttpRequest(url="https://upsilon.example/jobs", params={"q": "python"})
    )

    request, _ = transports.sent[0]
    assert (response.status_code, response.text) == (200, "ok")
    assert str(request.url) == "https://upsilon.example/jobs?q=python"
    assert request.headers["User-Agent"].startswith("Mozilla/5.0")


@pytest.mark.parametrize(
    ("method", "body", "expected_content", "expected_type"),
    [
        (
            "POST",
            {"query": {"text": "python"}, "page": 1},
            b'{"query":{"text":"python"},"page":1}',
            "application/json",
        ),
        ("GET", None, b"", None),
    ],
    ids=["post-with-body", "get-without-body"],
)
async def test_polite_client_sends_json_body(
    method: str, body: dict[str, object] | None, expected_content: bytes, expected_type: str | None
) -> None:
    transports = FakeTransportFactory()
    client = build_polite_client(transports, FakeTimer())

    await client.send_request(
        HttpRequest(url="https://psi.example/jobs", method=method, body=body)
    )

    request, _ = transports.sent[0]
    assert request.method == method
    assert request.content.replace(b" ", b"") == expected_content
    assert request.headers.get("Content-Type") == expected_type


async def test_polite_client_waits_between_requests_to_same_host() -> None:
    timer = FakeTimer()
    client = build_polite_client(FakeTransportFactory(), timer)

    for url in ["https://phi.example/a", "https://phi.example/b", "https://chi.example/a"]:
        await client.send_request(HttpRequest(url=url))

    assert timer.waits == [1.0]


def has_synthetic_root(context: ssl.SSLContext) -> bool:
    subjects = [dict(part[0] for part in cert["subject"]) for cert in context.get_ca_certs()]
    return {"commonName": "Synthetic Test Root CA"} in subjects


async def test_polite_client_uses_extra_ca_only_for_configured_hosts() -> None:
    transports = FakeTransportFactory()
    client = build_polite_client(
        transports,
        FakeTimer(),
        extra_ca_hosts=frozenset({"psi.example"}),
        extra_ca_path=SYNTHETIC_ROOT_CA,
    )

    await client.send_request(HttpRequest(url="https://psi.example/jobs"))
    await client.send_request(HttpRequest(url="https://omega.example/jobs"))

    contexts = {request.url.host: context for request, context in transports.sent}
    assert has_synthetic_root(contexts["psi.example"]) is True
    assert has_synthetic_root(contexts["omega.example"]) is False
    assert all(context.verify_mode == ssl.CERT_REQUIRED for context in contexts.values())


async def test_polite_client_wraps_network_error(caplog: pytest.LogCaptureFixture) -> None:
    transports = FakeTransportFactory(error=httpx.ConnectError("connection refused"))
    client = build_polite_client(transports, FakeTimer())

    with pytest.raises(HttpRequestError, match=r"GET https://alpha\.example/jobs failed"):
        await client.send_request(HttpRequest(url="https://alpha.example/jobs"))

    assert "connection refused" in caplog.text


async def test_polite_client_does_not_follow_redirects() -> None:
    # A redirect must not carry the extra root certificate or skip the per-host wait.
    transports = FakeTransportFactory(redirect_to="https://elsewhere.example/jobs")
    client = build_polite_client(transports, FakeTimer())

    response = await client.send_request(HttpRequest(url="https://psi.example/jobs"))

    assert response.status_code == 302
    assert [request.url.host for request, _ in transports.sent] == ["psi.example"]


def test_create_polite_client_warns_without_extra_ca_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = SourcesConfig.model_validate(
        {
            "sources": [
                {
                    "id": "chi-jobs",
                    "company": "Chi",
                    "technique": "json_endpoint",
                    "extra_ca": True,
                    "list": {"url": "https://chi.example/jobs"},
                }
            ]
        }
    )

    create_polite_client(Settings(_env_file=None), config)

    assert "extra root certificate for chi.example" in caplog.text
    assert "CV_JOB_MATCH_EXTRA_CA_PATH is not set" in caplog.text
