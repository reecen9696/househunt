"""Source parsers. Each source has its own module; all emit lists of
RawRow (see model.py) behind the shared normalisation step. Selectors and
JSON paths live in config, not code.
"""
from __future__ import annotations

from typing import Any


def dig(node: Any, dotted_path: str, default=None):
    """Navigate nested dicts by a dotted path from config.

    Missing keys return default rather than raising — a structural break is
    surfaced by the parse canary, not a traceback in row parsing.
    """
    if dotted_path is None:
        return default
    for part in dotted_path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node
