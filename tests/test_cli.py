from pathlib import Path

import pytest

from cv_job_match.settings import Settings
from cv_job_match.utils.cli import run_cli
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
