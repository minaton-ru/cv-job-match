import re
from pathlib import Path

import pytest

from cv_job_match.app import App, build_app
from cv_job_match.exceptions import (
    HttpRequestError,
    SourcesConfigError,
    UnknownSourceError,
    UnsupportedTechniqueError,
)
from cv_job_match.http_client import HttpRequest, HttpResponse
from cv_job_match.settings import Settings
from tests.fakes import FakeHttpClient, build_json_response
from tests.sources_files import write_sources_yaml

LIST_URL = "https://careers.alpha.example/api/jobs"
DETAIL_URL = "https://careers.alpha.example/api/job"


def build_sources_yaml(list_block: str, detail_block: str = "") -> str:
    return (
        "sources:\n"
        "  - id: alpha-backend\n"
        "    company: Alpha Corp\n"
        "    technique: json_endpoint\n"
        f"    list: {list_block}\n" + (f"    detail: {detail_block}\n" if detail_block else "")
    )


SOURCES_YAML = build_sources_yaml(
    f"{{url: '{LIST_URL}', items_path: data.items, id_field: id, fields: {{title: name}}}}"
)


def build_checked_app(
    tmp_path: Path, responses: dict[str, HttpResponse | Exception], text: str = SOURCES_YAML
) -> tuple[App, FakeHttpClient]:
    http = FakeHttpClient(responses)
    settings = Settings(sources_path=write_sources_yaml(tmp_path, text), _env_file=None)
    return build_app(settings, http=http), http


def build_list_response(items: object) -> HttpResponse:
    return build_json_response({"data": {"items": items}})


@pytest.mark.parametrize(
    ("items", "expected_count"),
    [
        (
            [{"id": 1, "name": "Backend Engineer"}, {"id": 2, "name": "Data Engineer"}, {"id": 3}],
            3,
        ),
        ([], 0),
    ],
    ids=["three-items", "empty-list-is-not-suspect"],
)
async def test_check_source_counts_listed_vacancies(
    tmp_path: Path, items: list[object], expected_count: int
) -> None:
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_list_response(items)})

    summary = await app.discovery.check_source("alpha-backend")

    assert summary.found_count == expected_count
    assert summary.suspect_reason is None


async def test_check_source_maps_configured_fields(tmp_path: Path) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', items_path: '', id_field: meta.ref, "
        "fields: {title: info.name, url: link}}"
    )
    items = [
        {"meta": {"ref": "A-7"}, "info": {"name": "Platform Engineer"}, "link": "/jobs/A-7"},
        {"meta": {"ref": 8}, "info": {}},
    ]
    app, _ = build_checked_app(tmp_path, {LIST_URL: build_json_response(items)}, text)

    summary = await app.discovery.check_source("alpha-backend")

    first, second = summary.previews
    assert (first.vacancy_id, first.title, first.url) == ("A-7", "Platform Engineer", "/jobs/A-7")
    assert second.vacancy_id == "8"
    assert second.title is None
    assert second.url is None


async def test_check_source_sends_list_params_for_first_page_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', params: {{q: python, limit: 50}}, items_path: data.items, "
        "id_field: id, pagination: {style: page, param: p}}"
    )
    app, http = build_checked_app(tmp_path, {LIST_URL: build_list_response([])}, text)

    await app.discovery.check_source("alpha-backend")

    assert http.requests == [HttpRequest(url=LIST_URL, params={"q": "python", "limit": "50"})]
    assert "pagination is not supported yet" in caplog.text


async def test_check_source_fills_id_into_description_url(tmp_path: Path) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', items_path: data.items, id_field: id}}",
        f"{{url: '{DETAIL_URL}/{{id}}', params: {{ref: 'job-{{id}}', lang: en}}, "
        "description_fields: [body.summary, body.missing, body.duties]}",
    )
    detail = {"body": {"summary": "Build APIs.", "duties": "Run\n them."}}
    responses = {
        LIST_URL: build_list_response([{"id": 41}]),
        f"{DETAIL_URL}/41": build_json_response(detail),
    }
    app, http = build_checked_app(tmp_path, responses, text)

    summary = await app.discovery.check_source("alpha-backend")

    assert http.requests[1] == HttpRequest(
        url=f"{DETAIL_URL}/41", params={"ref": "job-41", "lang": "en"}
    )
    assert summary.previews[0].description == "Build APIs. Run them."
    assert summary.previews[0].description_error is None


async def test_check_source_without_detail_previews_without_description(tmp_path: Path) -> None:
    app, http = build_checked_app(tmp_path, {LIST_URL: build_list_response([{"id": 1}])})

    summary = await app.discovery.check_source("alpha-backend")

    assert summary.previews[0].description is None
    assert summary.previews[0].description_error is None
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (HttpRequestError("connection refused"), r"request failed: connection refused"),
        (build_json_response({"error": "busy"}, status_code=503), rf"HTTP 503 from {LIST_URL}"),
        (HttpResponse(status_code=200, text="<html>"), rf"malformed JSON from {LIST_URL}"),
        (build_json_response({"data": {}}), r"items path 'data\.items' does not hold a list"),
        (build_list_response({"id": 1}), r"items path 'data\.items' does not hold a list"),
        (build_list_response([{"id": 1}, {"name": "No id"}]), r"list item has no ID at 'id'"),
        (build_list_response([{"id": None}]), r"list item has no ID"),
        (build_list_response([{"id": ""}]), r"list item has no ID"),
        (build_list_response([{"id": "   "}]), r"list item has no ID"),
    ],
    ids=[
        "network-error",
        "non-2xx",
        "malformed-json",
        "missing-items-path",
        "items-not-a-list",
        "item-without-id",
        "null-id",
        "empty-id",
        "whitespace-id",
    ],
)
async def test_check_source_reports_failed_list_fetch_as_suspect(
    tmp_path: Path, response: HttpResponse | Exception, expected_reason: str
) -> None:
    app, _ = build_checked_app(tmp_path, {LIST_URL: response})

    summary = await app.discovery.check_source("alpha-backend")

    assert summary.found_count == 0
    assert summary.previews == ()
    assert summary.suspect_reason is not None
    assert re.search(expected_reason, summary.suspect_reason)


async def test_check_source_notes_failed_description_in_preview(tmp_path: Path) -> None:
    text = build_sources_yaml(
        f"{{url: '{LIST_URL}', items_path: data.items, id_field: id}}",
        f"{{url: '{DETAIL_URL}/{{id}}', description_fields: [text]}}",
    )
    responses = {
        LIST_URL: build_list_response([{"id": 1}, {"id": 2}]),
        f"{DETAIL_URL}/1": build_json_response({"text": "Write code."}),
        f"{DETAIL_URL}/2": build_json_response({"other": "x"}),
    }
    app, _ = build_checked_app(tmp_path, responses, text)

    summary = await app.discovery.check_source("alpha-backend")

    assert summary.suspect_reason is None
    assert summary.previews[0].description == "Write code."
    assert summary.previews[1].description is None
    assert summary.previews[1].description_error == "no description fields found for Vacancy 2"


async def test_check_source_rejects_unknown_source_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app, _ = build_checked_app(tmp_path, {})

    with pytest.raises(UnknownSourceError, match="unknown Source 'beta-missing'"):
        await app.discovery.check_source("beta-missing")

    assert "Unknown Source id 'beta-missing'" in caplog.text


async def test_check_source_rejects_technique_without_adapter(tmp_path: Path) -> None:
    text = "sources:\n  - {id: gamma-html, company: Gamma, technique: html_css}\n"
    app, _ = build_checked_app(tmp_path, {}, text)

    with pytest.raises(
        UnsupportedTechniqueError, match="Source 'gamma-html': technique 'html_css' is not"
    ):
        await app.discovery.check_source("gamma-html")


@pytest.mark.parametrize(
    ("list_block", "detail_block", "expected_problem"),
    [
        ("{items_path: items, id_field: id}", "", r"list\.url: Field required"),
        (f"{{url: '{LIST_URL}', id_field: id}}", "", r"list\.items_path: Field required"),
        (f"{{url: '{LIST_URL}', items_path: items}}", "", r"list\.id_field: Field required"),
        (f"{{url: '{LIST_URL}', items_path: items, id_field: '  '}}", "", r"list\.id_field: .*"),
        (
            f"{{url: '{LIST_URL}', method: POST, items_path: items, id_field: id}}",
            "",
            r"list\.method: Input should be 'GET'",
        ),
        (
            f"{{url: '{LIST_URL}', items_path: items, id_field: id}}",
            f"{{url: '{DETAIL_URL}/{{id}}'}}",
            r"detail\.description_fields: Field required",
        ),
    ],
    ids=["no-url", "no-items-path", "no-id-field", "blank-id-field", "post", "no-description"],
)
async def test_check_source_rejects_invalid_options(
    tmp_path: Path, list_block: str, detail_block: str, expected_problem: str
) -> None:
    app, _ = build_checked_app(tmp_path, {}, build_sources_yaml(list_block, detail_block))

    with pytest.raises(SourcesConfigError, match=rf"Source 'alpha-backend': {expected_problem}"):
        await app.discovery.check_source("alpha-backend")
