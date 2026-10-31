import pytest

from cv_job_match.prefilter import find_prefilter_reason
from cv_job_match.profile import PrefilterRules

RULES = PrefilterRules(
    title_keywords=["python", "backend"],
    excluded_title_keywords=["senior director"],
    cities=["Example City"],
)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"title": "Java Developer", "city": "Example City"}, "title has none of"),
        ({"title": "Senior Director of Python", "city": None}, "title has 'senior director'"),
        ({"title": "Python Developer", "city": "Other Town"}, "city 'Other Town' is not"),
        ({"title": "Pythonista Engineer", "city": None}, "title has none of"),
    ],
)
def test_find_prefilter_reason_rejects_mismatch(
    fields: dict[str, str | None], reason: str
) -> None:
    found = find_prefilter_reason(RULES, fields)

    assert found is not None
    assert reason in found


@pytest.mark.parametrize(
    ("rules", "fields"),
    [
        (RULES, {"title": "Backend PYTHON Engineer", "city": "Example City, North"}),
        (RULES, {"title": "Senior Directorate Python Developer", "city": None}),
        (RULES, {"title": None, "city": None}),
        (RULES, {"title": "", "city": ""}),
        (RULES, {"title": "   ", "city": "   "}),
        (RULES, {}),
        (PrefilterRules(), {"title": "Java Developer", "city": "Other Town"}),
    ],
)
def test_find_prefilter_reason_passes(
    rules: PrefilterRules, fields: dict[str, str | None]
) -> None:
    assert find_prefilter_reason(rules, fields) is None
