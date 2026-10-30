from pathlib import Path

import pytest

from cv_job_match.app import App, build_app
from cv_job_match.http_client import HttpResponse
from cv_job_match.settings import Settings
from cv_job_match.utils.cli import run_cli
from tests.fakes import FakeHttpClient, build_json_response
from tests.sources_files import write_sources_yaml

SOURCES_YAML = """
sources:
  - {id: kappa-backend, company: Kappa, technique: json_endpoint}
  - {id: kappa-data, company: Kappa, technique: html_css, interval: 12h}
  - {id: lambda-all, company: Lambda, technique: embedded_state, enabled: false}
"""


def write_sources(tmp_path: Path, text: str) -> str:
    return str(write_sources_yaml(tmp_path, text))


def test_check_config_lists_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_cli(
        ["--sources", write_sources(tmp_path, SOURCES_YAML), "check-config"],
        Settings(_env_file=None),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "3 Sources, 2 enabled" in output
    assert "kappa-data  Kappa  html_css  every 12h  on" in output
    assert "lambda-all  Lambda  embedded_state  every 24h  off" in output


def test_check_config_reports_invalid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sources = write_sources(tmp_path, "sources:\n  - {id: mu, company: Mu, technique: ftp}\n")

    exit_code = run_cli(["--sources", sources, "check-config"], Settings(_env_file=None))

    assert exit_code == 3
    assert "Source 'mu': technique" in capsys.readouterr().err


def test_run_cli_without_command_shows_usage(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_cli([], Settings(_env_file=None))

    assert exit_code == 2
    assert "usage:" in capsys.readouterr().err


LIST_URL = "https://careers.nu.example/api/jobs"
DETAIL_URL = "https://careers.nu.example/api/job"

JSON_SOURCES_YAML = f"""
sources:
  - id: nu-backend
    company: Nu
    technique: json_endpoint
    list:
      url: {LIST_URL}
      items_path: jobs
      id_field: id
      fields: {{title: title, url: link}}
    detail:
      url: {DETAIL_URL}/{{id}}
      description_fields: [text]
"""


def run_check_source(
    tmp_path: Path, source_id: str, responses: dict[str, HttpResponse | Exception]
) -> int:
    def build_app_with_fake_http(settings: Settings) -> App:
        return build_app(settings, http=FakeHttpClient(responses))

    sources = write_sources(tmp_path, JSON_SOURCES_YAML)
    return run_cli(
        ["--sources", sources, "check-source", source_id],
        Settings(_env_file=None),
        app_factory=build_app_with_fake_http,
    )


def test_check_source_prints_count_and_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = [{"id": n, "title": f"Engineer {n}", "link": f"/jobs/{n}"} for n in range(1, 5)]
    responses: dict[str, HttpResponse | Exception] = {
        LIST_URL: build_json_response({"jobs": jobs}),
        **{
            f"{DETAIL_URL}/{n}": build_json_response({"text": f"Duties of job {n}. " * 30})
            for n in range(1, 3)
        },
    }

    exit_code = run_check_source(tmp_path, "nu-backend", responses)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "nu-backend: 4 Vacancies found (dry run, nothing saved)" in output
    assert "1  Engineer 1  /jobs/1" in output
    assert "3  Engineer 3  /jobs/3" in output
    assert "Engineer 4" not in output
    assert "Duties of job 2. Duties of job 2." in output
    assert "…" in output
    assert f"description unavailable: request failed: no canned response for {DETAIL_URL}/3" in (
        output
    )
    assert "Suspect" not in output


def test_check_source_prints_suspect_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    responses: dict[str, HttpResponse | Exception] = {
        LIST_URL: build_json_response({}, status_code=503)
    }

    exit_code = run_check_source(tmp_path, "nu-backend", responses)

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "nu-backend: 0 Vacancies found" in output
    assert f"Suspect: HTTP 503 from {LIST_URL}" in output


def test_check_source_reports_unknown_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = run_check_source(tmp_path, "xi-missing", {})

    assert exit_code == 2
    assert "unknown Source 'xi-missing'" in capsys.readouterr().err


def test_run_cli_reports_bad_extra_ca_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(extra_ca_path=tmp_path / "absent.pem", _env_file=None)

    exit_code = run_cli(
        ["--sources", write_sources(tmp_path, SOURCES_YAML), "check-config"], settings
    )

    assert exit_code == 3
    assert "cannot load extra root certificate" in capsys.readouterr().err


def test_run_cli_returns_config_error_for_invalid_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(profile_path=tmp_path / "absent", _env_file=None)

    exit_code = run_cli(
        ["--sources", write_sources(tmp_path, SOURCES_YAML), "check-config"], settings
    )

    assert exit_code == 3
    assert "Invalid Profile" in capsys.readouterr().err
