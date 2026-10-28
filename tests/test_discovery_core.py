from datetime import UTC, datetime

import pytest

from cv_job_match.discovery import (
    ListedVacancy,
    NewVacancy,
    RunRecord,
    SourceState,
    detect_changes,
    plan_detail_fetches,
)

NOW = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
BASELINE_RUN = RunRecord(
    source_id="alpha-backend",
    company="Alpha Corp",
    started_at=datetime(2026, 9, 30, 6, 0, tzinfo=UTC),
    status="baseline",
    found_count=1,
    error=None,
    new_vacancies=(),
)


def build_state(*known_ids: str, baseline_done: bool = True) -> SourceState:
    return SourceState(
        source_id="alpha-backend",
        company="Alpha Corp",
        last_successful_run=BASELINE_RUN if baseline_done else None,
        known_vacancy_ids=frozenset(known_ids),
    )


def build_listed(vacancy_id: str, **fields: str | None) -> ListedVacancy:
    return ListedVacancy(vacancy_id, {"title": f"Role {vacancy_id}", **fields})


@pytest.mark.parametrize(
    ("listed", "state", "expected_ids"),
    [
        ([build_listed("1")], build_state(), ["1"]),
        ([build_listed("1"), build_listed("2")], build_state("1"), ["2"]),
        ([build_listed("1", description="Full text")], build_state(), []),
        ([build_listed("1", description=None)], build_state(), ["1"]),
        ([build_listed("1"), build_listed("1")], build_state(), ["1"]),
        ([], build_state(), []),
        ([build_listed("1")], build_state(baseline_done=False), []),
    ],
    ids=[
        "new-without-text",
        "known-is-skipped",
        "list-has-full-text",
        "list-text-is-none",
        "duplicate-planned-once",
        "empty-list",
        "baseline-run-fetches-nothing",
    ],
)
def test_plan_detail_fetches(
    listed: list[ListedVacancy], state: SourceState, expected_ids: list[str]
) -> None:
    assert plan_detail_fetches(listed, state) == expected_ids


def build_new(vacancy_id: str, description: str | None, *, is_baseline: bool) -> NewVacancy:
    return NewVacancy(vacancy_id, {"title": f"Role {vacancy_id}"}, description, is_baseline)


@pytest.mark.parametrize(
    ("state", "listed", "texts", "expected_status", "expected_count", "expected_new"),
    [
        (
            build_state(baseline_done=False),
            [build_listed("1"), build_listed("2")],
            {},
            "baseline",
            2,
            (build_new("1", None, is_baseline=True), build_new("2", None, is_baseline=True)),
        ),
        (build_state(baseline_done=False), [], {}, "baseline", 0, ()),
        (
            build_state("1"),
            [build_listed("1"), build_listed("2")],
            {"2": "Text 2"},
            "ok",
            2,
            (build_new("2", "Text 2", is_baseline=False),),
        ),
        (
            build_state(),
            [build_listed("3", description="List text")],
            {},
            "ok",
            1,
            (
                NewVacancy(
                    "3", {"title": "Role 3", "description": "List text"}, "List text", False
                ),
            ),
        ),
        (
            build_state(),
            [build_listed("4"), build_listed("5")],
            {"5": "Text 5"},
            "ok",
            2,
            (build_new("5", "Text 5", is_baseline=False),),
        ),
        (
            build_state(),
            [build_listed("6")],
            {"6": None},
            "ok",
            1,
            (build_new("6", None, is_baseline=False),),
        ),
        (
            build_state(),
            [build_listed("7"), build_listed("7")],
            {"7": "Text 7"},
            "ok",
            1,
            (build_new("7", "Text 7", is_baseline=False),),
        ),
        (build_state("1"), [], {}, "ok", 0, ()),
    ],
    ids=[
        "first-success-is-baseline",
        "empty-first-success-is-baseline",
        "new-after-baseline",
        "description-from-list",
        "failed-description-left-out",
        "no-description-configured",
        "duplicate-recorded-once",
        "empty-after-baseline",
    ],
)
def test_detect_changes(
    state: SourceState,
    listed: list[ListedVacancy],
    texts: dict[str, str | None],
    expected_status: str,
    expected_count: int,
    expected_new: tuple[NewVacancy, ...],
) -> None:
    record = detect_changes(state, listed, None, texts, NOW)

    assert record == RunRecord(
        source_id="alpha-backend",
        company="Alpha Corp",
        started_at=NOW,
        status=expected_status,
        found_count=expected_count,
        error=None,
        new_vacancies=expected_new,
    )


@pytest.mark.parametrize("baseline_done", [False, True], ids=["first-run", "after-baseline"])
def test_detect_changes_records_failed_list_as_suspect(baseline_done: bool) -> None:
    state = build_state("1", baseline_done=baseline_done)

    record = detect_changes(state, [], "HTTP 503", {}, NOW)

    assert (record.status, record.error, record.found_count) == ("suspect", "HTTP 503", 0)
    assert record.new_vacancies == ()
