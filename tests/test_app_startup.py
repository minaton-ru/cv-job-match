from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cv_job_match.app import build_app
from cv_job_match.exceptions import SourcesConfigError
from cv_job_match.settings import Settings
from tests.fakes import FakeClock, FakeDependency
from tests.sources_files import write_sources_yaml

VALID_SOURCES_YAML = """
defaults:
  interval: 1d
sources:
  - id: alpha-backend
    company: Alpha Corp
    technique: json_endpoint
    notes: Backend roles only
    list:
      url: https://careers.alpha.example/api/jobs
      items_path: data.items
  - id: alpha-data
    company: Alpha Corp
    technique: html_css
    interval: 12h
    list:
      url: https://careers.alpha.example/search
  - id: beta-all
    company: Beta Ltd
    technique: embedded_state
    enabled: false
"""


def build_settings(tmp_path: Path, text: str) -> Settings:
    return Settings(sources_path=write_sources_yaml(tmp_path, text), _env_file=None)


def test_build_app_loads_company_with_two_sources(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path, VALID_SOURCES_YAML))

    alpha_ids = [source.id for source in app.sources.sources if source.company == "Alpha Corp"]
    assert alpha_ids == ["alpha-backend", "alpha-data"]


def test_build_app_applies_interval_override(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path, VALID_SOURCES_YAML))

    intervals = {source.id: app.sources.resolve_interval(source) for source in app.sources.sources}
    assert intervals["alpha-data"] == timedelta(hours=12)
    assert intervals["alpha-backend"] == timedelta(days=1)


def test_build_app_uses_one_day_interval_without_defaults(tmp_path: Path) -> None:
    text = "sources:\n  - {id: solo, company: Solo Inc, technique: html_css}\n"

    app = build_app(build_settings(tmp_path, text))

    assert app.sources.resolve_interval(app.sources.sources[0]) == timedelta(days=1)


def test_build_app_keeps_switched_off_source(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path, VALID_SOURCES_YAML))

    enabled = {source.id: source.enabled for source in app.sources.sources}
    assert enabled == {"alpha-backend": True, "alpha-data": True, "beta-all": False}


def test_build_app_keeps_technique_options(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path, VALID_SOURCES_YAML))

    options = app.sources.sources[0].options
    assert options == {
        "list": {"url": "https://careers.alpha.example/api/jobs", "items_path": "data.items"}
    }


def test_build_app_gives_empty_options_when_source_has_none(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path, VALID_SOURCES_YAML))

    assert app.sources.sources[2].options == {}


def test_build_app_rejects_source_missing_company(tmp_path: Path) -> None:
    text = "sources:\n  - {id: gamma-search, technique: html_css}\n"

    with pytest.raises(SourcesConfigError, match=r"Source 'gamma-search'.*company.*required"):
        build_app(build_settings(tmp_path, text))


def test_build_app_rejects_unknown_technique(tmp_path: Path) -> None:
    text = "sources:\n  - {id: delta-jobs, company: Delta, technique: screen_scraper}\n"

    with pytest.raises(
        SourcesConfigError, match=r"Source 'delta-jobs': technique: .*got 'screen_scraper'"
    ):
        build_app(build_settings(tmp_path, text))


def test_build_app_rejects_malformed_yaml(tmp_path: Path) -> None:
    text = "sources:\n  - id: broken\n   company: [unclosed\n"

    with pytest.raises(SourcesConfigError, match="malformed YAML"):
        build_app(build_settings(tmp_path, text))


@pytest.mark.parametrize("text", ["", "   \n\t\n", "# only a comment\n"])
def test_build_app_rejects_empty_file(tmp_path: Path, text: str) -> None:
    with pytest.raises(SourcesConfigError, match="file is empty"):
        build_app(build_settings(tmp_path, text))


def test_build_app_rejects_missing_file(tmp_path: Path) -> None:
    settings = Settings(sources_path=tmp_path / "absent.yaml", _env_file=None)

    with pytest.raises(SourcesConfigError, match="cannot read file"):
        build_app(settings)


@pytest.mark.parametrize("text", ["- just\n- a list\n", "plain text\n"])
def test_build_app_rejects_non_mapping_top_level(tmp_path: Path, text: str) -> None:
    with pytest.raises(SourcesConfigError, match="top level must be a mapping"):
        build_app(build_settings(tmp_path, text))


def test_build_app_rejects_empty_sources_list(tmp_path: Path) -> None:
    with pytest.raises(SourcesConfigError, match="sources: .*at least 1"):
        build_app(build_settings(tmp_path, "sources: []\n"))


@pytest.mark.parametrize("interval", ["", " ", "soon", "0h", "5w", "-1d"])
def test_build_app_rejects_invalid_interval(tmp_path: Path, interval: str) -> None:
    text = (
        f"sources:\n  - {{id: eps, company: Eps, technique: html_css, interval: '{interval}'}}\n"
    )

    with pytest.raises(SourcesConfigError, match=r"Source 'eps': interval: .*invalid duration"):
        build_app(build_settings(tmp_path, text))


def test_build_app_names_source_without_id_by_position(tmp_path: Path) -> None:
    text = (
        "sources:\n"
        "  - {id: first, company: Zeta, technique: html_css}\n"
        "  - {company: Zeta, technique: html_css}\n"
    )

    with pytest.raises(SourcesConfigError, match=r"Source #2: id: Field required"):
        build_app(build_settings(tmp_path, text))


@pytest.mark.parametrize("field", ["id", "company"])
def test_build_app_rejects_blank_source_field(tmp_path: Path, field: str) -> None:
    values = {"id": "eta", "company": "Eta", field: "   "}
    text = (
        f"sources:\n  - {{id: '{values['id']}', company: '{values['company']}', "
        "technique: html_css}\n"
    )

    with pytest.raises(SourcesConfigError, match=rf"{field}: .*blank"):
        build_app(build_settings(tmp_path, text))


def test_build_app_rejects_duplicate_source_ids(tmp_path: Path) -> None:
    text = (
        "sources:\n"
        "  - {id: theta, company: Theta, technique: html_css}\n"
        "  - {id: theta, company: Theta, technique: json_endpoint}\n"
    )

    with pytest.raises(SourcesConfigError, match="duplicate Source id 'theta'"):
        build_app(build_settings(tmp_path, text))


def test_build_app_logs_config_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = "sources:\n  - {id: iota, technique: html_css}\n"

    with pytest.raises(SourcesConfigError):
        build_app(build_settings(tmp_path, text))

    assert "Source 'iota': company: Field required" in caplog.text


def test_build_app_uses_given_fakes(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 1, 2, 3, 4, tzinfo=UTC))
    repos, llm = FakeDependency("repos"), FakeDependency("llm")
    http, notifier = FakeDependency("http"), FakeDependency("notifier")

    app = build_app(
        build_settings(tmp_path, VALID_SOURCES_YAML),
        repos=repos,
        llm=llm,
        http=http,
        notifier=notifier,
        clock=clock,
    )

    assert app.clock.now_utc() == datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    assert (app.repos, app.llm, app.http, app.notifier) == (repos, llm, http, notifier)


def test_build_app_uses_system_clock_by_default(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path, VALID_SOURCES_YAML))

    assert app.clock.now_utc().tzinfo is UTC


def test_settings_read_sources_path_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources_path = write_sources_yaml(tmp_path, VALID_SOURCES_YAML)
    monkeypatch.setenv("CV_JOB_MATCH_SOURCES_PATH", str(sources_path))

    app = build_app(Settings(_env_file=None))

    assert app.settings.sources_path == sources_path


def test_build_app_rejects_misspelled_source_key(tmp_path: Path) -> None:
    # Regression: a typo such as "enable" was kept as a technique option, so the
    # Source stayed switched on.
    text = "sources:\n  - {id: nu, company: Nu, technique: html_css, enable: false}\n"

    with pytest.raises(SourcesConfigError, match=r"Source 'nu': unknown key 'enable'"):
        build_app(build_settings(tmp_path, text))


@pytest.mark.parametrize("interval", ["0", "-5", "3600", "true"])
def test_build_app_rejects_non_text_interval(tmp_path: Path, interval: str) -> None:
    # Regression: numbers bypassed duration parsing and became seconds.
    text = f"sources:\n  - {{id: xi, company: Xi, technique: html_css, interval: {interval}}}\n"

    with pytest.raises(SourcesConfigError, match=r"Source 'xi': interval: .*invalid duration"):
        build_app(build_settings(tmp_path, text))


@pytest.mark.parametrize("field", ["id", "company", "technique"])
def test_build_app_rejects_null_required_field(tmp_path: Path, field: str) -> None:
    values = {"id": "omicron", "company": "Omicron", "technique": "html_css", field: "null"}
    text = "sources:\n  - {" + ", ".join(f"{key}: {value}" for key, value in values.items())
    text += "}\n"

    with pytest.raises(SourcesConfigError, match=rf"{field}: "):
        build_app(build_settings(tmp_path, text))


def test_build_app_uses_default_interval_when_interval_is_null(tmp_path: Path) -> None:
    text = "sources:\n  - {id: pi, company: Pi, technique: html_css, interval: null}\n"

    app = build_app(build_settings(tmp_path, text))

    assert app.sources.resolve_interval(app.sources.sources[0]) == timedelta(days=1)
