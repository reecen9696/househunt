"""Parse canary (§11).

Selector rot fails in the direction of "no properties found", which looks
identical to a quiet week. These checks make structural drift loud instead.
Each returns a list of problem strings; anything non-empty is prominently
reported and flips the run's exit code.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def check_entry(suburb_count: int, canary_cfg: dict) -> list[str]:
    problems = []
    min_suburbs = int(canary_cfg.get("min_entry_suburbs", 100))
    if suburb_count < min_suburbs:
        problems.append(
            f"Entry page indexed only {suburb_count} suburbs (expected >= {min_suburbs}). "
            "Likely structure drift or a blocked/challenge page."
        )
    return problems


def check_suburb_parse_rate(attempted: int, parsed_ok: int, canary_cfg: dict) -> list[str]:
    problems = []
    if attempted == 0:
        return ["No suburb pages were attempted — nothing matched the configured suburb list?"]
    rate = parsed_ok / attempted
    min_rate = float(canary_cfg.get("min_suburb_parse_rate", 0.8))
    if rate < min_rate:
        problems.append(
            f"Only {parsed_ok}/{attempted} suburb pages parsed ({rate:.0%}, "
            f"threshold {min_rate:.0%}). Structure drift or rate limiting."
        )
    return problems


def check_weekly_volume(this_week_leads: int, history: list[tuple[str, int]],
                        canary_cfg: dict) -> list[str]:
    """history: [(week_ending, lead_count)] most recent first, incl. this week."""
    prior = [n for _, n in history[1:5]]
    if not prior:
        return []
    avg = sum(prior) / len(prior)
    if avg == 0:
        return []
    ratio = this_week_leads / avg
    min_ratio = float(canary_cfg.get("min_weekly_ratio", 0.3))
    if ratio < min_ratio:
        return [
            f"Non-sale count this week ({this_week_leads}) is {ratio:.0%} of the "
            f"trailing average ({avg:.0f}). If the market didn't stop, the parser did."
        ]
    return []
