import json
import re
from pathlib import Path

import pytest

from cv_job_match.app import App, build_app
from cv_job_match.exceptions import HttpRequestError, SourcesConfigError
from cv_job_match.http_client import HttpRequest, HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import CannedAnswer, FakeHttpClient, build_html_response
from tests.sources_files import write_sources_yaml

LIST_URL = "https://careers.gamma.example/jobs"
DETAIL_URL = "https://careers.gamma.example/job"

LIST_BLOCK = (
    f"{{url: '{LIST_URL}', script_selector: 'script#app-state', items_path: props.jobs, "
    "id_field: ref, fields: {title: name, url: link}}"
)


def build_sources_yaml(list_block: str = LIST_BLOCK, detail_block: str = "") -> str:
    return (
        "sources:\n"
        "  - id: gamma-state\n"
        "    company: Gamma Corp\n"
        "    technique: embedded_state\n"
        f"    list: {list_block}\n" + (f"    detail: {detail_block}\n" if detail_block else "")
    )


def build_checked_app(
    tmp_path: Path,
    responses: dict[str, CannedAnswer | list[CannedAnswer]],
    text: str | None = None,
) -> tuple[App, FakeHttpClient]:
    http = FakeHttpClient(responses)
    sources_path = write_sources_yaml(tmp_path, text or build_sources_yaml())
    return build_app(Settings(sources_path=sources_path, _env_file=None), http=http), http


def build_state_page(state: object, script_id: str = "app-state") -> str:
    return (
        '<div id="root"></div>'
        f'<script id="{script_id}" type="application/json">{json.dumps(state)}</script>'
    )


JOBS = [
    {"ref": "G-1", "name": "Backend Engineer", "link": "/jobs/G-1"},
    {"ref": 2, "name": "SRE"},
]


@pytest.mark.parametrize(
    ("assignment_option", "script"),
    [
        ("", f'<script id="app-state">{json.dumps({"props": {"jobs": JOBS}})}</script>'),
        (
            ", assignment: window.__STATE__",
            '<script id="app-state">var x = 1;\n'
            f"window.__STATE__ = {json.dumps({'props': {'jobs': JOBS}})};\n"
            "window.__READY__ = true;</script>",
        ),
        (
            ", assignment: __STATE__",
            '<script id="app-state">if (window.__STATE__ == null) {}\n'
            f"window.__STATE__={json.dumps({'props': {'jobs': JOBS}})}</script>",
        ),
    ],
    ids=["json-script", "assigned-state", "short-assignment-name"],
)
async def test_check_source_reads_embedded_state_list(
    tmp_path: Path, assignment_option: str, script: str
) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', script_selector: 'script#app-state', items_path: props.jobs, "
        f"id_field: ref, fields: {{title: name, url: link}}{assignment_option}}}"
    )
    app, http = build_checked_app(tmp_path, {LIST_URL: build_html_response(script)}, text)

    summary = await app.discovery.check_source("gamma-state")

    assert summary.suspect_reason is None
    assert summary.found_count == 2
    assert [(p.vacancy_id, p.title, p.url) for p in summary.previews] == [
        ("G-1", "Backend Engineer", "/jobs/G-1"),
        ("2", "SRE", None),
    ]
    assert len(http.requests) == 1


async def test_check_source_counts_empty_embedded_state_list(tmp_path: Path) -> None:
    page = build_state_page({"props": {"jobs": []}})
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_html_response(page)})

    summary = await app.discovery.check_source("gamma-state")

    assert summary.found_count == 0
    assert summary.previews == ()
    assert summary.suspect_reason is None


@pytest.mark.parametrize(
    ("job", "expected_title", "expected_url"),
    [
        ({"ref": "G-1", "name": "", "link": "   "}, None, None),
        ({"ref": "G-1", "name": "\n\t", "link": None}, None, None),
        (
            {"ref": " G-1 ", "name": "  QA Engineer ", "link": " /jobs/G-1"},
            "QA Engineer",
            "/jobs/G-1",
        ),
    ],
    ids=["empty-and-whitespace", "newline-and-null", "padded"],
)
async def test_check_source_normalizes_blank_embedded_state_fields(
    tmp_path: Path, job: dict[str, object], expected_title: str | None, expected_url: str | None
) -> None:
    page = build_state_page({"props": {"jobs": [job]}})
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_html_response(page)})

    summary = await app.discovery.check_source("gamma-state")

    preview = summary.previews[0]
    assert (preview.vacancy_id, preview.title, preview.url) == (
        "G-1",
        expected_title,
        expected_url,
    )


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (HttpRequestError("connection reset"), r"request failed: connection reset"),
        (build_html_response("", status_code=503), rf"HTTP 503 from {LIST_URL}"),
        (
            build_html_response(build_state_page({}, script_id="other-state")),
            r"no element matches script selector 'script#app-state'",
        ),
        (
            build_html_response('<script id="app-state">  </script>'),
            r"malformed state in 'script#app-state'",
        ),
        (
            build_html_response('<script id="app-state">{"props": {"jobs": [</script>'),
            r"malformed state in 'script#app-state'",
        ),
        (
            HttpResponse(status_code=200, text='<script id="app-state">{"props"'),
            r"malformed state in 'script#app-state'",
        ),
        (
            build_html_response(build_state_page({"props": {}})),
            r"items path 'props\.jobs' does not hold a list",
        ),
        (
            build_html_response(build_state_page({"props": {"jobs": [{"name": "No ref"}]}})),
            r"list item has no ID at 'ref'",
        ),
        (
            build_html_response(build_state_page({"props": {"jobs": [{"ref": "  "}]}})),
            r"list item has no ID at 'ref'",
        ),
    ],
    ids=[
        "network-error",
        "non-2xx",
        "missing-script",
        "empty-script",
        "malformed-state",
        "truncated-page",
        "missing-items-path",
        "item-without-id",
        "whitespace-id",
    ],
)
async def test_check_source_reports_missing_embedded_state_as_suspect(
    tmp_path: Path, response: CannedAnswer, expected_reason: str
) -> None:
    app, _ = build_checked_app(tmp_path, {LIST_URL: response})

    summary = await app.discovery.check_source("gamma-state")

    assert summary.found_count == 0
    assert summary.previews == ()
    assert summary.suspect_reason is not None
    assert re.search(expected_reason, summary.suspect_reason)


@pytest.mark.parametrize(
    "script",
    [
        "window.__OTHER__ = {};",
        'window.APP__STATE__ = {"props": {"jobs": []}};',
        'if (window.__STATE__ == {"props": {"jobs": []}}) {}',
    ],
    ids=["other-name", "longer-name", "comparison"],
)
async def test_check_source_reports_missing_assignment_as_suspect(
    tmp_path: Path, script: str
) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', script_selector: 'script#app-state', "
        "assignment: __STATE__, items_path: props.jobs, id_field: ref}"
    )
    page = f'<script id="app-state">{script}</script>'
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_html_response(page)}, text)

    summary = await app.discovery.check_source("gamma-state")

    assert summary.found_count == 0
    assert summary.suspect_reason == "state '__STATE__' not found in script"


async def test_check_source_fetches_embedded_state_description(tmp_path: Path) -> None:
    text = build_sources_yaml(
        detail_block=f"{{url: '{DETAIL_URL}', params: {{ref: '{{id}}'}}, "
        "script_selector: 'script#app-state', "
        "description_fields: [job.summary, job.missing, job.blank, job.duties]}"
    )
    detail_state = {"job": {"summary": "Build APIs.", "blank": "  ", "duties": "Run them."}}
    responses = {
        LIST_URL: build_html_response(build_state_page({"props": {"jobs": JOBS[:1]}})),
        DETAIL_URL: build_html_response(build_state_page(detail_state)),
    }
    app, http = build_checked_app(tmp_path, responses, text)

    summary = await app.discovery.check_source("gamma-state")

    assert http.requests[1] == HttpRequest(url=DETAIL_URL, params={"ref": "G-1"})
    assert summary.previews[0].description == "Build APIs. Run them."
    assert summary.previews[0].description_error is None


@pytest.mark.parametrize(
    ("detail_page", "expected_error"),
    [
        (build_state_page({"job": {}}), "no description fields found for Vacancy G-1"),
        ("<p>Gone</p>", "no element matches script selector 'script#app-state'"),
    ],
    ids=["no-fields", "no-state"],
)
async def test_check_source_notes_failed_embedded_state_description(
    tmp_path: Path, detail_page: str, expected_error: str
) -> None:
    text = build_sources_yaml(
        detail_block=f"{{url: '{DETAIL_URL}/{{id}}', script_selector: 'script#app-state', "
        "description_fields: [job.summary]}"
    )
    responses = {
        LIST_URL: build_html_response(build_state_page({"props": {"jobs": JOBS[:1]}})),
        f"{DETAIL_URL}/G-1": build_html_response(detail_page),
    }
    app, _ = build_checked_app(tmp_path, responses, text)

    summary = await app.discovery.check_source("gamma-state")

    assert summary.suspect_reason is None
    assert summary.previews[0].description is None
    assert summary.previews[0].description_error == expected_error


STATE_OPTIONS = "script_selector: script, items_path: jobs, id_field: ref"


@pytest.mark.parametrize(
    ("list_block", "detail_block", "expected_problem"),
    [
        (f"{{{STATE_OPTIONS}}}", "", r"list\.url: Field required"),
        (
            f"{{url: '{LIST_URL}', items_path: jobs, id_field: ref}}",
            "",
            r"list\.script_selector: Field required",
        ),
        (
            f"{{url: '{LIST_URL}', script_selector: 'script[', items_path: jobs, id_field: ref}}",
            "",
            r"list\.script_selector: invalid CSS selector 'script\['",
        ),
        (
            f"{{url: '{LIST_URL}', script_selector: script, id_field: ref}}",
            "",
            r"list\.items_path: Field required",
        ),
        (
            f"{{url: '{LIST_URL}', {STATE_OPTIONS}, assignment: '  '}}",
            "",
            r"list\.assignment: must not be blank",
        ),
        (
            f"{{url: '{LIST_URL}', {STATE_OPTIONS}}}",
            f"{{url: '{DETAIL_URL}', script_selector: script, description_fields: [text]}}",
            r"detail: \{id\} must appear in url or params",
        ),
        (
            f"{{url: '{LIST_URL}', {STATE_OPTIONS}}}",
            f"{{url: '{DETAIL_URL}/{{id}}', description_fields: [text]}}",
            r"detail\.script_selector: Field required",
        ),
    ],
    ids=[
        "no-url",
        "no-script-selector",
        "invalid-script-selector",
        "no-items-path",
        "blank-assignment",
        "detail-without-id",
        "detail-without-script-selector",
    ],
)
async def test_check_source_rejects_invalid_embedded_state_options(
    tmp_path: Path, list_block: str, detail_block: str, expected_problem: str
) -> None:
    app, _ = build_checked_app(tmp_path, {}, build_sources_yaml(list_block, detail_block))

    with pytest.raises(SourcesConfigError, match=rf"Source 'gamma-state': {expected_problem}"):
        await app.discovery.check_source("gamma-state")
