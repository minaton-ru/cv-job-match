"""Pre-filter: cheap keyword rules that screen out obvious mismatches before any AI."""

import logging
import re
from collections.abc import Mapping

from cv_job_match.constants import CITY_FIELD, TITLE_FIELD
from cv_job_match.profile import PrefilterRules

logger = logging.getLogger(__name__)


def find_prefilter_reason(rules: PrefilterRules, fields: Mapping[str, str | None]) -> str | None:
    """Check a Vacancy's list fields against the Pre-filter rules.

    Keywords and cities match as case-insensitive whole words, so word forms such
    as plurals need keywords of their own. A missing, empty or
    whitespace-only title or city is not an obvious mismatch, so it never rejects,
    and an empty keyword or city list switches its rule off.

    Args:
        rules: The Candidate's Pre-filter rules.
        fields: The Vacancy's list fields, such as ``title`` and ``city``.

    Returns:
        Why the Vacancy is filtered out, or ``None`` when it passes.
    """
    title = normalize_field_value(fields.get(TITLE_FIELD))
    city = normalize_field_value(fields.get(CITY_FIELD))
    logger.debug("Pre-filtering title %r, city %r", title, city)
    if title is not None:
        excluded = find_contained_items(title, rules.excluded_title_keywords)
        if excluded:
            return f"title has {quote_items(excluded)}"
        if rules.title_keywords and not find_contained_items(title, rules.title_keywords):
            return f"title has none of {quote_items(rules.title_keywords)}"
    if city is not None and rules.cities and not find_contained_items(city, rules.cities):
        return f"city {city!r} is not one of {quote_items(rules.cities)}"
    return None


def normalize_field_value(value: str | None) -> str | None:
    """Strip a field value and read a blank one as no value.

    Args:
        value: The raw field value.

    Returns:
        The stripped value, or ``None`` when it is missing or blank.
    """
    if value is None:
        return None
    return value.strip() or None


def find_contained_items(text: str, items: list[str]) -> list[str]:
    """Find the items that occur in a text as whole words, ignoring case.

    Args:
        text: The text to search.
        items: Keywords or cities to look for.

    Returns:
        The items found, in their configured order.
    """
    return [
        item
        for item in items
        if re.search(rf"(?<!\w){re.escape(item)}(?!\w)", text, re.IGNORECASE)
    ]


def quote_items(items: list[str]) -> str:
    """Quote and join items for a rejection reason.

    Args:
        items: Keywords or cities.

    Returns:
        The items quoted and separated by commas.
    """
    return ", ".join(repr(item) for item in items)
