"""Reading and writing the scrape criteria from the review page.

`config.yaml` stays the single source of truth — this module edits it in
place rather than introducing a second store that could disagree with it.
Two rules make that safe:

**Comments survive.** The config's comments are the tool's documentation:
why the budget filter reads the lower bound, why a null land size must be
included, what a suburbs_mode of `all` costs. Re-dumping the parsed document
erases all of it, and round-trip YAML libraries move comments around in ways
that are hard to predict — emptying a list silently ate an entire section
header in testing. So writes are surgical instead: only the exact lines of
the keys being changed are rewritten, and every other byte of the file is
passed through untouched. Comments sitting inside a value being replaced
(such as the note above the suburb list) are carried across explicitly.

**Only whitelisted fields can be written.** The panel can reach the filters
that describe *what we're looking for* and nothing else — never JSON paths,
outcome mappings, rate limits or the fetch layer. A typo in the browser
must not be able to silently disable the parse canary.

Everything is validated before anything is written, and the file is
replaced atomically, so a rejected value leaves the config untouched rather
than half-applied.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class SettingsError(ValueError):
    """A submitted value the config must not be allowed to take."""


# Property types REA actually labels listings with. Offered as checkboxes so
# the panel can't introduce a type the source never emits — which would
# filter everything out and look like a broken scan.
PROPERTY_TYPES = ["House", "Townhouse", "Unit", "Apartment", "Villa",
                  "Land", "Acreage", "Rural"]


def _money(name: str, value: Any) -> Optional[int]:
    if value in (None, "", "null"):
        return None
    try:
        n = int(float(str(value).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        raise SettingsError(f"{name} must be a number")
    if n <= 0:
        raise SettingsError(f"{name} must be greater than zero")
    if n > 100_000_000:
        raise SettingsError(f"{name} looks wrong — over $100m")
    return n


def _count(name: str, value: Any, limit: int = 10) -> Optional[int]:
    if value in (None, "", "null"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise SettingsError(f"{name} must be a whole number")
    if not 0 <= n <= limit:
        raise SettingsError(f"{name} must be between 0 and {limit}")
    return n


def _area(name: str, value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise SettingsError(f"{name} must be a number")
    if n < 0:
        raise SettingsError(f"{name} cannot be negative")
    return n


def _string_list(name: str, value: Any) -> list[str]:
    """Accepts a list, or a comma-separated string from a text input."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise SettingsError(f"{name} must be a list")
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if len(text) > 80:
            raise SettingsError(f"{name}: '{text[:30]}…' is too long")
        if text not in out:
            out.append(text)
    return out


def _suburbs(name: str, value: Any) -> list[str]:
    names = _string_list(name, value)
    if not names:
        # An empty list with mode `list` fetches nothing and reports a clean
        # zero-result week, which is indistinguishable from a quiet market.
        raise SettingsError("Pick at least one suburb, or the scan has "
                            "nothing to fetch")
    return names


def _types(name: str, value: Any) -> list[str]:
    """Empty means every type — that's the config's own convention."""
    chosen = _string_list(name, value)
    known = {t.lower(): t for t in PROPERTY_TYPES}
    out = []
    for t in chosen:
        if t.lower() not in known:
            raise SettingsError(f"Unknown property type '{t}'")
        out.append(known[t.lower()])
    return out


@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]      # where it lives in config.yaml
    parse: Callable[[str, Any], Any]
    default: Any = None


# The whitelist. Nothing outside this can be written by the panel.
FIELDS: dict[str, Field] = {
    "suburbs": Field(("filters", "suburbs"), _suburbs, []),
    "price_ceiling": Field(("filters", "price_ceiling"), _money, None),
    "stretch_ceiling": Field(("filters", "stretch_ceiling"), _money, None),
    "min_bedrooms": Field(("filters", "min_bedrooms"), _count, None),
    "property_types": Field(("filters", "property_types"), _types, []),
    "min_land_size_sqm": Field(("filters", "min_land_size_sqm"), _area, None),
    "exclude_streets": Field(("filters", "exclude_streets"), _string_list, []),
    "exclude_agencies": Field(("filters", "exclude_agencies"), _string_list, []),
}


def _dig(node, path: tuple[str, ...]):
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _render(key: str, value: Any, indent: int,
            kept_comments: list[str]) -> list[str]:
    """The replacement lines for one key, in this file's own style."""
    pad = " " * indent
    if isinstance(value, list):
        if not value:
            # A block sequence with no items renders as a bare key, which
            # reads as null rather than "none of these".
            return [f"{pad}{key}: []"]
        out = [f"{pad}{key}:"]
        out.extend(kept_comments)
        out.extend(f"{pad}  - {item}" for item in value)
        return out
    if value is None:
        rendered = "null"
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, float) and value.is_integer():
        rendered = str(int(value))
    else:
        rendered = str(value)
    return [f"{pad}{key}: {rendered}"] + kept_comments


def _splice(text: str, section: str, key: str, value: Any) -> str:
    """Replace one key's value in `section`, touching no other line.

    The span replaced runs from the key's own line to the last line that is
    part of its value — stopping at the first comment or at any line indented
    no deeper than the key, because those belong to whatever comes next. That
    boundary is what keeps a section header sitting below a list from being
    swallowed when the list is emptied.
    """
    lines = text.split("\n")

    section_at = next(
        (i for i, line in enumerate(lines)
         if re.match(rf"^{re.escape(section)}\s*:", line)), None)
    if section_at is None:
        raise SettingsError(f"config.yaml has no '{section}:' section")

    # The section ends at the next line that starts in column zero.
    section_end = len(lines)
    for i in range(section_at + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line[0].isspace():
            section_end = i
            break

    key_at = next(
        (i for i in range(section_at + 1, section_end)
         if re.match(rf"^\s+{re.escape(key)}\s*:", lines[i])), None)
    if key_at is None:
        raise SettingsError(f"config.yaml has no '{section}.{key}'")

    indent = len(lines[key_at]) - len(lines[key_at].lstrip())

    # Where the value stops: the first line that is neither blank nor a
    # comment and is indented no deeper than the key. That line belongs to
    # the next setting.
    stop = section_end
    for i in range(key_at + 1, section_end):
        line = lines[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(stripped) <= indent:
            stop = i
            break

    # The last line that is actually part of the value. Anything after it —
    # including a comment block introducing the next setting, or a section
    # header — is outside the span and must be left exactly where it is.
    last = key_at
    for i in range(key_at + 1, stop):
        stripped = lines[i].lstrip()
        if stripped and not stripped.startswith("#"):
            last = i

    # A comment sitting between the key and its first item documents this
    # setting (the note above the suburb list), so it is carried across.
    kept_comments = [lines[i] for i in range(key_at + 1, last)
                     if lines[i].strip().startswith("#")]

    replacement = _render(key, value, indent, kept_comments)
    return "\n".join(lines[:key_at] + replacement + lines[last + 1:])


def _load(config_path: Path | str) -> dict:
    import yaml
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def read_settings(config_path: Path | str) -> dict:
    """Current criteria, plus the choices the panel offers."""
    doc = _load(config_path)
    values = {}
    for name, field in FIELDS.items():
        raw = _dig(doc, field.path)
        if isinstance(raw, list):
            raw = [str(v) for v in raw]
        values[name] = field.default if raw is None else raw
    return {
        "values": values,
        "options": {"property_types": PROPERTY_TYPES},
        # Shown so it's obvious the panel is editing a real file, and which.
        "config_path": str(config_path),
        "suburbs_mode": _dig(doc, ("filters", "suburbs_mode")) or "list",
    }


def write_settings(config_path: Path | str, updates: dict) -> dict:
    """Validate every submitted field, then write the file atomically.

    Fields the caller didn't submit are left exactly as they are, so the
    panel never has to round-trip settings it doesn't show.
    """
    config_path = Path(config_path)
    unknown = set(updates) - set(FIELDS)
    if unknown:
        raise SettingsError(f"Not editable here: {', '.join(sorted(unknown))}")

    # Parse everything first: a later failure must not leave earlier fields
    # already written.
    parsed: dict[str, Any] = {}
    for name, value in updates.items():
        parsed[name] = FIELDS[name].parse(name, value)

    ceiling = parsed.get("price_ceiling")
    stretch = parsed.get("stretch_ceiling")
    if ceiling and stretch and stretch <= ceiling:
        raise SettingsError(
            "Stretch ceiling must be above the price ceiling — it's the band "
            "of results shown separately rather than dropped")

    text = config_path.read_text()
    for name, value in parsed.items():
        section, key = FIELDS[name].path
        text = _splice(text, section, key, value)

    # Re-parse before committing: a splice that produced invalid YAML, or that
    # landed a value somewhere unexpected, must never reach the real file —
    # the scan reads this config and a broken one breaks the weekly run.
    import yaml
    try:
        check = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SettingsError(f"Refusing to save — the edit produced invalid "
                            f"YAML ({e.__class__.__name__})")
    for name, value in parsed.items():
        got = _dig(check, FIELDS[name].path)
        if isinstance(value, list):
            got = [str(v) for v in (got or [])]
            expected = [str(v) for v in value]
        else:
            expected = value
        if got != expected:
            raise SettingsError(
                f"Refusing to save — {name} did not land correctly "
                f"(wrote {expected!r}, file reads {got!r})")

    # Write beside the target so the atomic replace stays on one filesystem,
    # and never leave a partially written config behind if this dies midway.
    fd, tmp = tempfile.mkstemp(dir=str(config_path.parent),
                               prefix=".config-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, config_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    logger.info("Settings saved to %s: %s", config_path,
                ", ".join(sorted(parsed)))
    return read_settings(config_path)
