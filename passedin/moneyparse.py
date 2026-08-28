"""Parsing price text into integers.

Handles: "$786,000", "$1,050,000 - $1,150,000", "$1.2m", "Last bid $690,000",
"Vendor bid: $850k", "Contact Agent" (-> None). Never guesses: text without
an explicit dollar figure yields None.
"""
from __future__ import annotations

import re
from typing import Optional

_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([mk])?", re.IGNORECASE)


def parse_money(text: str | None) -> Optional[int]:
    """First dollar figure in the text, as an int, or None."""
    if not text:
        return None
    m = _MONEY.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    if suffix == "m":
        value *= 1_000_000
    elif suffix == "k":
        value *= 1_000
    value = int(round(value))
    # Guard against fragments like "$45" in unrelated page text when parsing
    # listing HTML: real property figures are 5+ digits.
    if value < 10_000:
        return None
    return value


def parse_money_range(text: str | None) -> tuple[Optional[int], Optional[int]]:
    """(low, high) from a range like "$1,050,000 - $1,150,000".

    A single figure returns (value, value). No figure returns (None, None).
    """
    if not text:
        return None, None
    figures = []
    for m in _MONEY.finditer(text):
        value = float(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").lower()
        if suffix == "m":
            value *= 1_000_000
        elif suffix == "k":
            value *= 1_000
        value = int(round(value))
        if value >= 10_000:
            figures.append(value)
    if not figures:
        return None, None
    if len(figures) == 1:
        return figures[0], figures[0]
    return min(figures[:2]), max(figures[:2])
