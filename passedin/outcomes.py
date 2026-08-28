"""Raw outcome label -> canonical outcome mapping (§5).

Table-driven from config. Unrecognised labels are logged loudly and mapped
to UNKNOWN — never silently dropped: a silent drop hides exactly the
listings worth seeing.
"""
from __future__ import annotations

import logging

from .model import CANONICAL_OUTCOMES

logger = logging.getLogger(__name__)


class OutcomeMapper:
    def __init__(self, mapping_by_source: dict):
        self.mapping_by_source = {
            source: {str(k).strip().upper(): v for k, v in (table or {}).items()}
            for source, table in mapping_by_source.items()
        }
        self.unrecognised: dict[str, int] = {}  # "source:label" -> count

    def map(self, source: str, raw_label: str | None) -> str:
        if raw_label is None or str(raw_label).strip() == "":
            key = f"{source}:<empty>"
            self.unrecognised[key] = self.unrecognised.get(key, 0) + 1
            logger.warning("UNRECOGNISED OUTCOME LABEL (empty) from %s — emitting UNKNOWN", source)
            return "UNKNOWN"

        label = str(raw_label).strip().upper()
        table = self.mapping_by_source.get(source, {})
        canonical = table.get(label)
        if canonical is None:
            key = f"{source}:{label}"
            self.unrecognised[key] = self.unrecognised.get(key, 0) + 1
            logger.warning(
                "UNRECOGNISED OUTCOME LABEL %r from %s — emitting UNKNOWN. "
                "Add it to outcome_mapping.%s in config.yaml.",
                raw_label, source, source,
            )
            return "UNKNOWN"
        if canonical not in CANONICAL_OUTCOMES:
            logger.error(
                "Config maps %s:%s to %r, which is not a canonical outcome %s",
                source, label, canonical, sorted(CANONICAL_OUTCOMES),
            )
            return "UNKNOWN"
        return canonical
