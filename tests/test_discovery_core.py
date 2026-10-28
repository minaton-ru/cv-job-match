from datetime import UTC, datetime

import pytest

from cv_job_match.discovery import (
    KnownVacancy,
    ListedVacancy,
    NewVacancy,
    Revision,
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
    listed_ids=frozenset({"1"}),
    error=None,
    new_vacancies=(),
)


def build_state(*known_ids: str, baseline_done: bool = True) -> SourceState:
    return SourceState(
        source_id="alpha-backend",
        company="Alpha Corp",
        last_successful_run=BASELINE_RUN if baseline_done else None,
        known_vacancies={
            vacancy_id: KnownVacancy({"title": f"Role {vacancy_id}"}, is_closed=False)
            for vacancy_id in known_ids
        },
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
    ("state", "listed", "texts", "expected_status", "expected_ids", "expected_new"),
    [
        (
            build_state(baseline_done=False),
            [build_listed("1"), build_listed("2")],
            {},
            "baseline",
            {"1", "2"},
            (build_new("1", None, is_baseline=True), build_new("2", None, is_baseline=True)),
        ),
        (build_state(baseline_done=False), [], {}, "baseline", set(), ()),
        (
            build_state("1"),
            [build_listed("1"), build_listed("2")],
            {"2": "Text 2"},
            "ok",
            {"1", "2"},
            (build_new("2", "Text 2", is_baseline=False),),
        ),
        (
            build_state(),
            [build_listed("3", description="List text")],
            {},
            "ok",
            {"3"},
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
            {"4", "5"},
            (build_new("5", "Text 5", is_baseline=False),),
        ),
        (
            build_state(),
            [build_listed("6")],
            {"6": None},
            "ok",
            {"6"},
            (build_new("6", None, is_baseline=False),),
        ),
        (
            build_state(),
            [build_listed("7"), build_listed("7")],
            {"7": "Text 7"},
            "ok",
            {"7"},
            (build_new("7", "Text 7", is_baseline=False),),
        ),
    ],
    ids=[
        "first-success-is-baseline",
        "empty-first-success-is-baseline",
        "new-after-baseline",
        "description-from-list",
        "failed-description-left-out",
        "no-description-configured",
        "duplicate-recorded-once",
    ],
)
def test_detect_changes(
    state: SourceState,
    listed: list[ListedVacancy],
    texts: dict[str, str | None],
    expected_status: str,
    expected_ids: set[str],
    expected_new: tuple[NewVacancy, ...],
) -> None:
    record = detect_changes(state, listed, None, texts, NOW)

    assert record == RunRecord(
        source_id="alpha-backend",
        company="Alpha Corp",
        started_at=NOW,
        status=expected_status,
        listed_ids=frozenset(expected_ids),
        error=None,
        new_vacancies=expected_new,
    )


EMPTY_BASELINE_RUN = RunRecord(
    "alpha-backend", "Alpha Corp", BASELINE_RUN.started_at, "baseline", frozenset(), None, ()
)


@pytest.mark.parametrize(
    ("last_successful_run", "error", "expected_status", "expected_error", "expected_closed"),
    [
        (BASELINE_RUN, "HTTP 503", "suspect", "HTTP 503", set()),
        (None, "HTTP 503", "suspect", "HTTP 503", set()),
        (
            BASELINE_RUN,
            None,
            "suspect",
            "found 0 Vacancies, the last successful run found 1",
            set(),
        ),
        (EMPTY_BASELINE_RUN, None, "ok", None, {"1"}),
    ],
    ids=[
        "list-fetch-failed",
        "list-fetch-failed-on-first-run",
        "empty-after-nonempty-success",
        "empty-after-empty-success-is-not-suspect",
    ],
)
def test_detect_changes_suspect_rules(
    last_successful_run: RunRecord | None,
    error: str | None,
    expected_status: str,
    expected_error: str | None,
    expected_closed: set[str],
) -> None:
    state = SourceState("alpha-backend", "Alpha Corp", last_successful_run, {"1": build_known()})

    record = detect_changes(state, [], error, {}, NOW)

    assert (record.status, record.error, record.found_count) == (
        expected_status,
        expected_error,
        0,
    )
    assert record.closed_ids == expected_closed
    assert record.new_vacancies == ()


def build_known(
    title: str | None = "Role 1", *, is_closed: bool = False, **fields: str
) -> KnownVacancy:
    return KnownVacancy({"title": title, **fields}, is_closed)


def build_tracking_state(
    known: dict[str, KnownVacancy], listed_by_other_sources: frozenset[str] = frozenset()
) -> SourceState:
    return SourceState("alpha-backend", "Alpha Corp", BASELINE_RUN, known, listed_by_other_sources)


@pytest.mark.parametrize(
    ("state", "listed", "expected_revisions"),
    [
        (
            build_tracking_state({"1": build_known("Old")}),
            [build_listed("1", title="New")],
            (Revision("1", {"title": "New"}),),
        ),
        (build_tracking_state({"1": build_known()}), [build_listed("1")], ()),
        (
            build_tracking_state({"1": build_known()}),
            [build_listed("1", title=None)],
            (Revision("1", {"title": None}),),
        ),
        (
            build_tracking_state({"1": build_known(url="https://jobs.example/1")}),
            [build_listed("1")],
            (),
        ),
    ],
    ids=[
        "changed-field-is-revision",
        "unchanged-fields-no-revision",
        "field-becomes-missing-is-revision",
        "unmapped-field-not-compared",
    ],
)
def test_detect_changes_records_list_changes_as_revisions(
    state: SourceState, listed: list[ListedVacancy], expected_revisions: tuple[Revision, ...]
) -> None:
    record = detect_changes(state, listed, None, {}, NOW)

    assert record.revisions == expected_revisions
    assert record.new_vacancies == ()


@pytest.mark.parametrize(
    ("state", "listed", "expected_closed", "expected_reopened", "expected_revisions"),
    [
        (
            build_tracking_state(
                {"1": build_known(), "2": build_known("Role 2")}, frozenset({"1"})
            ),
            [build_listed("2")],
            set(),
            set(),
            (),
        ),
        (
            build_tracking_state({"1": build_known(), "2": build_known("Role 2")}),
            [build_listed("2")],
            {"1"},
            set(),
            (),
        ),
        (
            build_tracking_state({"1": build_known(is_closed=True), "2": build_known("Role 2")}),
            [build_listed("2")],
            set(),
            set(),
            (),
        ),
        (
            build_tracking_state({"1": build_known(is_closed=True)}),
            [build_listed("1")],
            set(),
            {"1"},
            (),
        ),
        (
            build_tracking_state({"1": build_known("Old", is_closed=True)}),
            [build_listed("1", title="New")],
            set(),
            {"1"},
            (Revision("1", {"title": "New"}),),
        ),
        (
            build_tracking_state({"1": build_known()}, frozenset({"1"})),
            [build_listed("1")],
            set(),
            set(),
            (),
        ),
    ],
    ids=[
        "dropped-but-listed-by-other-source-stays-open",
        "dropped-by-every-source-is-closed",
        "already-closed-not-closed-again",
        "closed-reappears-is-reopened",
        "closed-reappears-changed",
        "known-from-other-source-not-new",
    ],
)
def test_detect_changes_closes_and_reopens_vacancies(
    state: SourceState,
    listed: list[ListedVacancy],
    expected_closed: set[str],
    expected_reopened: set[str],
    expected_revisions: tuple[Revision, ...],
) -> None:
    record = detect_changes(state, listed, None, {}, NOW)

    assert record.closed_ids == expected_closed
    assert record.reopened_ids == expected_reopened
    assert record.revisions == expected_revisions
    assert record.new_vacancies == ()
