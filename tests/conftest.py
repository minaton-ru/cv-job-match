from pathlib import Path

import pytest

from tests.profile_files import write_profile


@pytest.fixture(autouse=True)
def isolate_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test in its own directory that holds a synthetic Profile.

    Args:
        tmp_path: The test's temporary directory.
        monkeypatch: Restores the working directory after the test.
    """
    monkeypatch.chdir(tmp_path)
    write_profile(tmp_path / "profile")
