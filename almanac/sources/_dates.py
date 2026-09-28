"""Date helpers for calendar pages."""

from __future__ import annotations

import html
import re

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def month_number(name: str) -> int:
    """'Sept.', 'September', 'Oct' -> 10 ... raises KeyError on anything else."""
    return MONTHS[name.strip(". ").lower()[:3]]


def text_of(fragment: str) -> str:
    """HTML fragment -> plain text with collapsed whitespace."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
