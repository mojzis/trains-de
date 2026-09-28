"""Paths, feed registry and routing defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "gtfs.duckdb"
OUT_DIR = Path("out")
PAIRS_DIR = Path("pairs")
FARES_PATH = Path("fares.toml")


@dataclass(frozen=True)
class FeedSpec:
    """A GTFS feed we know how to fetch, with its licence and attribution."""

    name: str
    url: str
    title: str
    licence: str
    attribution: str
    attribution_url: str
    default: bool = True
    notes: str = ""


FEEDS: dict[str, FeedSpec] = {
    spec.name: spec
    for spec in [
        FeedSpec(
            name="de_fv",
            url="https://download.gtfs.de/germany/fv_free/latest.zip",
            title="gtfs.de Germany long-distance rail (fv_free)",
            licence="CC BY 4.0",
            attribution="DELFI e.V. / gtfs.de",
            attribution_url="https://gtfs.de",
            notes="ICE/IC/EC plus the Czech legs of some international trains. "
            "Rolling ~30-day window.",
        ),
        FeedSpec(
            name="de_rv",
            url="https://download.gtfs.de/germany/rv_free/latest.zip",
            title="gtfs.de Germany regional rail (rv_free)",
            licence="CC BY 4.0",
            attribution="DELFI e.V. / gtfs.de",
            attribution_url="https://gtfs.de",
            notes="RE/RB/S-Bahn, and some long-distance lines published as bare "
            "numbers (e.g. line 27 Hamburg-Berlin-Dresden-Praha). "
            "Rolling ~30-day window.",
        ),
        FeedSpec(
            name="cz_czptt",
            url="https://motis.obehy.cz/get-feeds/cz-czptt-gtfs.zip",
            title="Czech rail timetable (CZPTT via JrUtil / obehy.cz)",
            licence="CC0 1.0",
            attribution="Správa železnic (CZPTT open data), converted by the "
            "Oběhy project",
            attribution_url="https://obehy.cz",
            default=False,
            notes="All Czech rail with train numbers, whole timetable year. "
            "Needed only for Czech domestic legs the German feeds lack "
            "(e.g. Praha-Cheb).",
        ),
    ]
}

DEFAULT_FEEDS = [name for name, spec in FEEDS.items() if spec.default]


@dataclass(frozen=True)
class RoutingConfig:
    """Tunable routing knobs."""

    hub_change_min: int = 8
    default_change_min: int = 5
    # Floor on every change time, transfers.txt included (0 = no floor). The
    # "comfortable changes" variant raises it so a small delay cannot break
    # the journey.
    min_change_min: int = 0
    max_rounds: int = 7
    max_rounds_regional: int = 9
    max_journeys: int = 12
    # A corridor is kept only if its fastest journey is within this factor of
    # the fastest journey overall (or within ``slack_hours`` of it).
    corridor_ratio: float = 1.5
    corridor_slack_hours: float = 2.0
    # Region: keep stations whose detour d(O,s)+d(s,D) <= ratio * d(O,D).
    region_ratio: float = 1.6
    region_margin_km: float = 60.0
    through_join_max_gap_s: int = 600
    border_radius_km: float = 30.0
    extra_hubs: tuple[str, ...] = field(default_factory=tuple)


# A change shorter than this is flagged as tight on the page.
TIGHT_CHANGE_MIN = 10

MERGE_RADIUS_M = 300.0

# Stations that are always hubs (8 min change), matched on normalised name.
HUB_NAMES = (
    "Praha hl.n.",
    "Dresden Hbf",
    "Berlin Hbf",
    "Hamburg Hbf",
    "Nürnberg Hbf",
    "Hannover Hbf",
    "Leipzig Hbf",
    "Magdeburg Hbf",
    "München Hbf",
    "Frankfurt(Main)Hbf",
    "Köln Hbf",
)
