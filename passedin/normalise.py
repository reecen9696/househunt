"""Address normalisation for identity and dedup (§9).

The same property appears with inconsistent formatting across sources:
"3/12 Smith St" vs "Unit 3, 12 Smith Street", "Sth Yarra" vs "South Yarra".
Normalising means: lowercase, expand abbreviations, standardise unit
notation to "unit/number street", strip punctuation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Street-type and directional abbreviations, expanded to full words.
_ABBREVIATIONS = {
    "st": "street",
    "rd": "road",
    "ave": "avenue",
    "av": "avenue",
    "cres": "crescent",
    "cr": "crescent",
    "crt": "court",
    "ct": "court",
    "pl": "place",
    "dr": "drive",
    "drv": "drive",
    "gr": "grove",
    "gve": "grove",
    "pde": "parade",
    "hwy": "highway",
    "tce": "terrace",
    "bvd": "boulevard",
    "blvd": "boulevard",
    "cl": "close",
    "wy": "way",
    "esp": "esplanade",
    "cct": "circuit",
    "gdns": "gardens",
    "sq": "square",
    "lne": "lane",
    "ln": "lane",
    # directionals
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "nth": "north",
    "sth": "south",
    "est": "east",
    "wst": "west",
    "mt": "mount",
}

_UNIT_PREFIX = re.compile(
    r"^(?:unit|apt|apartment|flat|villa|townhouse|shop|suite|lot)\s*(\w+)[,\s/]+\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class NormalisedAddress:
    norm: str                 # full normalised street address, e.g. "3/12 smith street"
    street_number: str        # "3/12" (unit/number) or "12"
    street: str               # "smith street"


def normalise_suburb(suburb: str) -> str:
    words = re.sub(r"[^\w\s]", " ", suburb.lower()).split()
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def normalise_address(address_raw: str) -> NormalisedAddress:
    a = address_raw.strip().lower()
    a = re.sub(r"\s+", " ", a)

    # "unit 3, 12 smith st" / "apt 3/12 smith st" -> "3/12 smith st"
    m = _UNIT_PREFIX.match(a)
    if m:
        a = f"{m.group(1)}/{m.group(2)}"

    # strip punctuation except the unit slash and hyphens inside ranges
    a = re.sub(r"[^\w\s/\-]", " ", a)
    a = re.sub(r"\s+", " ", a).strip()

    # split leading number token(s) from street words
    m = re.match(r"^([\d]+[a-z]?(?:\s*[/\-]\s*[\d]+[a-z]?)?)\s+(.*)$", a)
    if m:
        number = re.sub(r"\s", "", m.group(1))
        rest = m.group(2)
    else:
        number = ""
        rest = a

    words = [
        _ABBREVIATIONS.get(w, w)
        for w in rest.split()
    ]
    street = " ".join(words)
    norm = f"{number} {street}".strip()
    return NormalisedAddress(norm=norm, street_number=number, street=street)
