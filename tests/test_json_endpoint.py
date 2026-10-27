import pytest

from cv_job_match.adapters.json_endpoint import fill_vacancy_id_in_json


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            {"job": {"ref": "{id}"}, "ids": ["{id}", "x-{id}"]},
            {"job": {"ref": "41"}, "ids": ["41", "x-41"]},
        ),
        ({"page": 1, "flag": True, "none": None}, {"page": 1, "flag": True, "none": None}),
        ({}, {}),
        ("no placeholder", "no placeholder"),
        ("", ""),
    ],
    ids=["nested", "non-text-untouched", "empty-dict", "plain-text", "empty-text"],
)
def test_fill_vacancy_id_in_json(value: object, expected: object) -> None:
    assert fill_vacancy_id_in_json(value, "41") == expected
