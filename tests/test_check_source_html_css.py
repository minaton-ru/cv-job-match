import re
from pathlib import Path

import pytest

from cv_job_match.app import App, build_app
from cv_job_match.exceptions import HttpRequestError, SourcesConfigError
from cv_job_match.http_client import HttpRequest, HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import CannedAnswer, FakeHttpClient, build_html_response
from tests.sources_files import write_sources_yaml

LIST_URL = "https://careers.beta.example/jobs"
DETAIL_URL = "https://careers.beta.example/job"

LIST_BLOCK = (
    f"{{url: '{LIST_URL}', list_selector: ul.jobs, item_selector: li.job, "
    "id_field: {selector: a, attribute: data-id}, "
    "fields: {title: h3, url: {selector: a, attribute: href}}}"
)


def build_sources_yaml(list_block: str = LIST_BLOCK, detail_block: str = "") -> str:
    return (
        "sources:\n"
        "  - id: beta-html\n"
        "    company: Beta Corp\n"
        "    technique: html_css\n"
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


def build_job_item(vacancy_id: str, title: str) -> str:
    return (
        f'<li class="job"><h3>{title}</h3>'
        f'<a data-id="{vacancy_id}" href="/jobs/{vacancy_id}">Open</a></li>'
    )


def build_list_page(*items: str) -> str:
    return f'<header>Careers</header><ul class="jobs">{"".join(items)}</ul>'


async def test_check_source_reads_html_css_list(tmp_path: Path) -> None:
    page = build_list_page(
        build_job_item("B-1", "Backend Engineer"),
        build_job_item("B-2", "Data\n   Engineer"),
        '<li class="ad">Join our newsletter</li>',
    )
    app, http = build_checked_app(tmp_path, {LIST_URL: build_html_response(page)})

    summary = await app.discovery.check_source("beta-html")

    assert summary.suspect_reason is None
    assert summary.found_count == 2
    assert [(p.vacancy_id, p.title, p.url) for p in summary.previews] == [
        ("B-1", "Backend Engineer", "/jobs/B-1"),
        ("B-2", "Data Engineer", "/jobs/B-2"),
    ]
    assert len(http.requests) == 1


async def test_check_source_reads_html_css_id_from_item_itself(tmp_path: Path) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', list_selector: '#jobs', item_selector: article, "
        "id_field: {attribute: data-ref}, fields: {title: .name}}"
    )
    page = (
        '<section id="jobs"><article data-ref=" R-9 "><p class="name">QA</p></article></section>'
    )
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_html_response(page)}, text)

    summary = await app.discovery.check_source("beta-html")

    assert (summary.previews[0].vacancy_id, summary.previews[0].title) == ("R-9", "QA")


async def test_check_source_counts_empty_html_css_list(tmp_path: Path) -> None:
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_html_response(build_list_page())})

    summary = await app.discovery.check_source("beta-html")

    assert summary.found_count == 0
    assert summary.previews == ()
    assert summary.suspect_reason is None


@pytest.mark.parametrize(
    "item",
    [
        '<li class="job"><a data-id="B-1">Open</a></li>',
        '<li class="job"><h3></h3><a data-id="B-1" href="">Open</a></li>',
        '<li class="job"><h3>  \n\t </h3><a data-id="B-1" href="   ">Open</a></li>',
    ],
    ids=["missing-elements", "empty-values", "whitespace-values"],
)
async def test_check_source_maps_blank_html_css_fields_to_none(tmp_path: Path, item: str) -> None:
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_html_response(build_list_page(item))})

    summary = await app.discovery.check_source("beta-html")

    assert summary.previews[0].vacancy_id == "B-1"
    assert summary.previews[0].title is None
    assert summary.previews[0].url is None


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (HttpRequestError("connection reset"), r"request failed: connection reset"),
        (build_html_response("", status_code=502), rf"HTTP 502 from {LIST_URL}"),
        (
            HttpResponse(status_code=200, text='<ul class="jobs"<li class=job>>broken'),
            r"no element matches list selector 'ul\.jobs'",
        ),
        (
            HttpResponse(status_code=200, text='{"jobs": []}'),
            r"no element matches list selector 'ul\.jobs'",
        ),
        (
            build_html_response('<ol class="jobs"></ol>'),
            r"no element matches list selector 'ul\.jobs'",
        ),
        (
            build_html_response(build_list_page('<li class="job"><h3>No id</h3></li>')),
            r"list item has no ID at 'a' attribute 'data-id'",
        ),
        (
            build_html_response(build_list_page(build_job_item("", "Empty id"))),
            r"list item has no ID",
        ),
        (
            build_html_response(build_list_page(build_job_item("  ", "Blank id"))),
            r"list item has no ID",
        ),
    ],
    ids=[
        "network-error",
        "non-2xx",
        "malformed-html",
        "not-html",
        "missing-list-selector",
        "item-without-id",
        "empty-id",
        "whitespace-id",
    ],
)
async def test_check_source_reports_html_css_failure_as_suspect(
    tmp_path: Path, response: CannedAnswer, expected_reason: str
) -> None:
    app, _ = build_checked_app(tmp_path, {LIST_URL: response})

    summary = await app.discovery.check_source("beta-html")

    assert summary.found_count == 0
    assert summary.previews == ()
    assert summary.suspect_reason is not None
    assert re.search(expected_reason, summary.suspect_reason)


async def test_check_source_fetches_html_css_description(tmp_path: Path) -> None:
    text = build_sources_yaml(
        detail_block=f"{{url: '{DETAIL_URL}/{{id}}', params: {{ref: 'job-{{id}}', lang: en}}, "
        "description_selectors: [.summary, .missing, li.duty]}"
    )
    detail_page = (
        '<div class="summary"><p>Build  APIs.</p></div>'
        '<ul><li class="duty">Run them.</li><li class="duty">  </li>'
        '<li class="duty">Fix.</li></ul>'
    )
    responses = {
        LIST_URL: build_html_response(build_list_page(build_job_item("B-1", "Backend"))),
        f"{DETAIL_URL}/B-1": build_html_response(detail_page),
    }
    app, http = build_checked_app(tmp_path, responses, text)

    summary = await app.discovery.check_source("beta-html")

    assert http.requests[1] == HttpRequest(
        url=f"{DETAIL_URL}/B-1", params={"ref": "job-B-1", "lang": "en"}
    )
    assert summary.previews[0].description == "Build APIs. Run them. Fix."
    assert summary.previews[0].description_error is None


async def test_check_source_notes_missing_html_css_description(tmp_path: Path) -> None:
    text = build_sources_yaml(
        detail_block=f"{{url: '{DETAIL_URL}/{{id}}', description_selectors: [.summary]}}"
    )
    responses = {
        LIST_URL: build_html_response(build_list_page(build_job_item("B-1", "Backend"))),
        f"{DETAIL_URL}/B-1": build_html_response('<div class="summary">   </div>'),
    }
    app, _ = build_checked_app(tmp_path, responses, text)

    summary = await app.discovery.check_source("beta-html")

    assert summary.suspect_reason is None
    assert summary.previews[0].description is None
    assert summary.previews[0].description_error == "no description found for Vacancy B-1"


ITEM_OPTIONS = "list_selector: ul.jobs, item_selector: li, id_field: a"


@pytest.mark.parametrize(
    ("list_block", "detail_block", "expected_problem"),
    [
        (f"{{{ITEM_OPTIONS}}}", "", r"list\.url: Field required"),
        (
            f"{{url: '{LIST_URL}', item_selector: li, id_field: a}}",
            "",
            r"list\.list_selector: Field required",
        ),
        (
            f"{{url: '{LIST_URL}', list_selector: ul, item_selector: li}}",
            "",
            r"list\.id_field: Field required",
        ),
        (
            f"{{url: '{LIST_URL}', list_selector: '  ', item_selector: li, id_field: a}}",
            "",
            r"list\.list_selector: must not be blank",
        ),
        (
            f"{{url: '{LIST_URL}', list_selector: 'ul[', item_selector: li, id_field: a}}",
            "",
            r"list\.list_selector: invalid CSS selector 'ul\['",
        ),
        (
            f"{{url: '{LIST_URL}', list_selector: 'p::before', item_selector: li, id_field: a}}",
            "",
            r"list\.list_selector: invalid CSS selector 'p::before'",
        ),
        (
            f"{{url: '{LIST_URL}', {ITEM_OPTIONS}, fields: {{title: 'h3>>'}}}}",
            "",
            r"list\.fields\.title\.selector: invalid CSS selector 'h3>>'",
        ),
        (
            f"{{url: '{LIST_URL}', list_selector: ul, item_selector: li, "
            "id_field: {selector: a, attribute: '  '}}",
            "",
            r"list\.id_field\.attribute: must not be blank",
        ),
        (
            f"{{url: '{LIST_URL}', {ITEM_OPTIONS}}}",
            f"{{url: '{DETAIL_URL}', params: {{lang: en}}, description_selectors: [p]}}",
            r"detail: \{id\} must appear in url or params",
        ),
        (
            f"{{url: '{LIST_URL}', {ITEM_OPTIONS}}}",
            f"{{url: '{DETAIL_URL}/{{id}}', description_selectors: []}}",
            r"detail\.description_selectors: List should have at least 1 item",
        ),
    ],
    ids=[
        "no-url",
        "no-list-selector",
        "no-id-field",
        "blank-list-selector",
        "invalid-list-selector",
        "pseudo-element-selector",
        "invalid-field-selector",
        "blank-attribute",
        "detail-without-id",
        "no-description-selectors",
    ],
)
async def test_check_source_rejects_invalid_html_css_options(
    tmp_path: Path, list_block: str, detail_block: str, expected_problem: str
) -> None:
    app, _ = build_checked_app(tmp_path, {}, build_sources_yaml(list_block, detail_block))

    with pytest.raises(SourcesConfigError, match=rf"Source 'beta-html': {expected_problem}"):
        await app.discovery.check_source("beta-html")
