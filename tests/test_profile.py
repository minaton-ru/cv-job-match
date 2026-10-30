from pathlib import Path

import pytest

from cv_job_match.app import Repositories, build_app
from cv_job_match.exceptions import ProfileError, RepositoryNotConfiguredError
from cv_job_match.settings import Settings
from tests.fakes import FakeDiscoveryRepository, FakeProfileRepository
from tests.profile_files import CRITERIA_YAML, RESUME_MD, write_profile
from tests.sources_files import write_sources_yaml

SOURCES_YAML = "sources:\n  - {id: rho, company: Rho, technique: html_css}\n"


def build_settings(tmp_path: Path, profile_path: Path = Path("profile")) -> Settings:
    sources_path = write_sources_yaml(tmp_path, SOURCES_YAML)
    return Settings(sources_path=sources_path, profile_path=profile_path, _env_file=None)


def test_build_app_loads_valid_profile(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path))

    snapshot = app.profile.snapshot
    assert snapshot.resume == RESUME_MD
    assert snapshot.criteria.language == "en"
    assert snapshot.criteria.score_threshold == 60
    assert snapshot.criteria.excluded_roles == ["Sales Manager"]
    assert snapshot.criteria.prefilter.cities == ["Example City"]


@pytest.mark.parametrize(
    ("resume", "criteria", "message"),
    [
        (None, CRITERIA_YAML, "cannot read resume.md"),
        ("", CRITERIA_YAML, "resume.md is empty"),
        (" \n\t\n", CRITERIA_YAML, "resume.md is empty"),
        (RESUME_MD, None, "cannot read criteria.yaml"),
        (RESUME_MD, "language: [unclosed\n", "malformed YAML"),
        (RESUME_MD, "", "language: Field required"),
        (RESUME_MD, "language: '  '\n", "language: .*blank"),
        (RESUME_MD, "language: en\nscore_threshold: 101\n", "score_threshold: .*100"),
        (RESUME_MD, "language: en\nstrenghts: [Python]\n", "strenghts: Extra inputs"),
    ],
)
def test_build_app_rejects_invalid_profile(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    resume: str | None,
    criteria: str | None,
    message: str,
) -> None:
    profile_path = write_profile(tmp_path / "broken", resume, criteria)

    with pytest.raises(ProfileError, match=message):
        build_app(build_settings(tmp_path, profile_path))

    assert "Invalid Profile" in caplog.text


def test_build_app_rejects_missing_profile_folder(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="folder not found"):
        build_app(build_settings(tmp_path, tmp_path / "absent"))


def load_profile_version(tmp_path: Path, profile_path: Path) -> str:
    return build_app(build_settings(tmp_path, profile_path)).profile.snapshot.version


def test_profile_version_is_same_for_same_content(tmp_path: Path) -> None:
    first = load_profile_version(tmp_path, write_profile(tmp_path / "first"))
    second = load_profile_version(tmp_path, write_profile(tmp_path / "second"))

    assert first == second
    assert len(first) == 64


@pytest.mark.parametrize(
    ("resume", "criteria"),
    [(RESUME_MD + "Also Go.\n", CRITERIA_YAML), (RESUME_MD, CRITERIA_YAML + "# note\n")],
)
def test_profile_version_changes_on_any_edit(tmp_path: Path, resume: str, criteria: str) -> None:
    original = load_profile_version(tmp_path, write_profile(tmp_path / "original"))
    edited = load_profile_version(tmp_path, write_profile(tmp_path / "edited", resume, criteria))

    assert edited != original


async def test_profile_snapshot_saved_only_for_new_version(tmp_path: Path) -> None:
    repository = FakeProfileRepository()
    repos = Repositories(discovery=FakeDiscoveryRepository(), profile=repository)
    profile_path = write_profile(tmp_path / "snapshot")

    first = await build_app(
        build_settings(tmp_path, profile_path), repos=repos
    ).profile.save_snapshot_if_new()
    again = await build_app(
        build_settings(tmp_path, profile_path), repos=repos
    ).profile.save_snapshot_if_new()
    write_profile(profile_path, RESUME_MD + "Also Go.\n")
    edited = await build_app(
        build_settings(tmp_path, profile_path), repos=repos
    ).profile.save_snapshot_if_new()

    assert (first, again, edited) == (True, False, True)
    assert len(repository.snapshots) == 2


async def test_profile_snapshot_save_needs_repository(tmp_path: Path) -> None:
    app = build_app(build_settings(tmp_path))

    with pytest.raises(RepositoryNotConfiguredError, match="needs a repository"):
        await app.profile.save_snapshot_if_new()


def test_build_app_rejects_resume_not_in_utf8(tmp_path: Path) -> None:
    # Regression: a non-UTF-8 file raised UnicodeDecodeError, which the CLI did not catch.
    profile_path = write_profile(tmp_path / "latin1", resume=None)
    (profile_path / "resume.md").write_bytes("Résumé".encode("latin-1"))

    with pytest.raises(ProfileError, match="resume.md is not UTF-8 text"):
        build_app(build_settings(tmp_path, profile_path))
