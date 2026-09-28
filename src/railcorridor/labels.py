"""Derive a train label (``RJ 384``, ``ICE 25``, ``RE3``) and product class."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Categories that are not valid with a Deutschlandticket / regional-only fares.
LONG_DISTANCE = {
    "ICE",
    "IC",
    "EC",
    "ECE",
    "RJ",
    "RJX",
    "EN",
    "NJ",
    "FLX",
    "TGV",
    "EST",
    "D",
    "DRF",
    "EX",
    "SC",
    "LE",
    "AEX",
    "IR",
    "WB",
}
# Display spelling for categories that are not all caps.
_DISPLAY = {"OS": "Os", "SP": "Sp", "RX": "Rx", "EX": "Ex", "AEX": "AEx"}
# Regional products written without a space before the line number.
_COMPACT = {"RE", "RB", "S", "U", "RS", "MEX", "RR", "L", "T"}
_LONG_DISTANCE_AGENCY = re.compile(
    r"Fernverkehr|ÖBB|Ceske|České|ZSSK|MAV|PKP|SBB|SNCF|Dänische|Nederlandse",
    re.IGNORECASE,
)
_ROUTE_TYPE = {
    101: "ICE",
    102: "IC",
    103: "IR",
    105: "EN",
    106: "R",
    107: "R",
    109: "S",
    116: "Zahnradbahn",
}


@dataclass(frozen=True)
class Label:
    """Display label for a trip plus its product class."""

    text: str
    category: str
    long_distance: bool
    quality: int  # 3 train number, 2 lettered line, 1 derived, 0 route_type only


def derive_label(
    route_short_name: str | None,
    route_long_name: str | None,
    trip_short_name: str | None,
    agency_name: str | None,
    route_type: int,
) -> Label:
    """Label from trip_short_name / route_short_name / agency, else route_type."""
    for text, quality in ((trip_short_name, 3), (route_short_name, 2)):
        lab = _parse(text, quality)
        if lab:
            return lab
    rsn = (route_short_name or "").strip()
    m = re.fullmatch(r"(\d+)(N?)", rsn)
    if m and _LONG_DISTANCE_AGENCY.search(agency_name or ""):
        cat = "EN" if m[2] else "EC"
        return Label(f"{cat} {rsn}", cat, True, 1)
    lab = _parse(route_long_name, 1)
    if lab:
        return lab
    cat = _ROUTE_TYPE.get(route_type, "Zug")
    text = f"{cat} {rsn}".strip() if rsn else cat
    return Label(text, cat, cat in LONG_DISTANCE, 0)


def _parse(text: str | None, quality: int) -> Label | None:
    if not text or not text.strip():
        return None
    m = re.match(r"^\s*([A-Za-zÄÖÜäöü]+)\s*([0-9][0-9A-Za-z]*)?", text)
    if not m:
        return None
    raw, num = m[1], m[2] or ""
    up = raw.upper()
    cat = _DISPLAY.get(up, up)
    if quality == 3 and not num:
        return None  # a trip_short_name without a number is not a train name
    sep = "" if up in _COMPACT else " "
    label = f"{cat}{sep}{num}" if num else cat
    return Label(label, cat, up in LONG_DISTANCE, quality)
