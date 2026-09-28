"""Fare notes. GTFS carries no prices, so v1 reads a hand-edited ``fares.toml``.

``FareProvider`` is the seam for a later live-price implementation (e.g. a
db-vendo-client bestprice lookup); v1 never calls DB's private APIs.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class FareNote:
    """What the UI shows about a corridor's price."""

    summary: str  # short range, e.g. "Sparpreis ~EUR 30-60"
    note: str = ""
    source: str = "fares.toml (hand-edited estimate)"


class FareProvider(Protocol):
    """Anything that can describe the fare for a corridor."""

    def fare(
        self, corridor_id: str, *, regional: bool, date: dt.date
    ) -> FareNote | None:
        """Fare note for a corridor, or None when unknown."""
        ...

    def footnote(self) -> str:
        """General note shown under the corridor cards."""
        ...


class TomlFareProvider:
    """Fare notes from a TOML file: ``[corridors.<id>]`` plus defaults."""

    def __init__(self, path: Path) -> None:
        """Read ``path``; a missing file gives no fares."""
        self.data: dict = {}
        if path.exists():
            with path.open("rb") as fh:
                self.data = tomllib.load(fh)

    def fare(
        self,
        corridor_id: str,
        *,
        regional: bool,
        date: dt.date,
    ) -> FareNote | None:
        """Corridor entry, else the regional/default entry."""
        corridors = self.data.get("corridors", {})
        entry = corridors.get(corridor_id)
        if entry is None:
            entry = self.data.get("regional" if regional else "default")
        if not entry:
            return None
        return FareNote(summary=entry.get("summary", ""), note=entry.get("note", ""))

    def footnote(self) -> str:
        """The ``bahncard_note`` string."""
        return str(self.data.get("bahncard_note", ""))
