"""Run all searches for a city pair on one date and group the results."""

from __future__ import annotations

import datetime as dt
import multiprocessing as mp
import os
import time
import tomllib
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

import duckdb

from railcorridor.config import PAIRS_DIR, RoutingConfig
from railcorridor.corridors import Corridor, Places, group, pick_journeys, select
from railcorridor.load import loaded_feeds
from railcorridor.router import Journey, Network
from railcorridor.stations import find_station, slug
from railcorridor.timetable import Timetable, load_timetable, region_filter

DIRECTIONS = ("outbound", "return")


@dataclass
class PairConfig:
    """Settings for one city pair (see ``pairs/*.toml``)."""

    from_name: str
    to_name: str
    title: str = ""
    subtitle: str = ""
    places: list[str] = field(default_factory=list)
    search_via: list[str] = field(default_factory=list)
    expected: list[dict] = field(default_factory=list)

    @property
    def slug(self) -> str:
        """``praha-luneburg`` from ``Praha hl.n.`` / ``Lüneburg``."""
        return pair_slug(self.from_name, self.to_name)


def city(station_name: str) -> str:
    """First word of a station name: ``Praha hl.n.`` -> ``Praha``."""
    word = station_name.replace("-", " ").replace(",", " ").split()[0]
    return word.split("(")[0]


def pair_slug(a: str, b: str) -> str:
    """Pair id used for output directories."""
    return f"{slug(city(a))}-{slug(city(b))}"


def load_pair(from_name: str, to_name: str, pairs_dir: Path = PAIRS_DIR) -> PairConfig:
    """Pair config from ``pairs/<slug>.toml`` (either orientation) or defaults."""
    for a, b in ((from_name, to_name), (to_name, from_name)):
        path = pairs_dir / f"{pair_slug(a, b)}.toml"
        if path.exists():
            with path.open("rb") as fh:
                d = tomllib.load(fh)
            return PairConfig(
                from_name=from_name,
                to_name=to_name,
                title=d.get("title", ""),
                subtitle=d.get("subtitle", ""),
                places=list(d.get("places", [])),
                search_via=list(d.get("search_via", [])),
                expected=list(d.get("expected", [])),
            )
    return PairConfig(from_name=from_name, to_name=to_name)


@dataclass
class Result:
    """Everything the exporter needs."""

    pair: PairConfig
    date: dt.date
    window: tuple[int, int]
    tt: Timetable
    origin: int
    target: int
    corridors: list[Corridor]
    chosen: dict[str, list[tuple[Corridor, Journey]]]
    places: Places
    feeds: list[str]
    missing: dict[str, list[dict]]
    timings: dict[str, float]


def regional_view(tt: Timetable) -> Timetable:
    """Same timetable restricted to trips valid with regional tickets."""
    return replace(tt, trips=[t for t in tt.trips if not t.long_distance])


def auto_places(tt: Timetable, exclude: set[int], n: int = 12) -> list[str]:
    """Busiest long-distance places in the region, for pairs without config."""
    counts: Counter[str] = Counter()
    for t in tt.trips:
        if not t.long_distance:
            continue
        for s in t.stations:
            if s not in exclude:
                counts[city(tt.names[s])] += 1
    return [p for p, _ in counts.most_common(n)]


def explore(
    con: duckdb.DuckDBPyConnection,
    pair: PairConfig,
    date: dt.date,
    window: tuple[int, int],
    *,
    both_directions: bool = True,
    regional: bool = True,
    cfg: RoutingConfig | None = None,
    log=print,
) -> Result:
    """Search journeys in both directions and group them into corridors."""
    cfg = cfg or RoutingConfig()
    timings: dict[str, float] = {}
    feeds = loaded_feeds(con)
    o_id, o_name = find_station(con, pair.from_name)
    d_id, d_name = find_station(con, pair.to_name)
    log(f"{o_name} ({o_id}) -> {d_name} ({d_id}) on {date:%a %Y-%m-%d}")

    st = con.execute("SELECT station_id, lat, lon FROM main.stations").fetchall()
    pos = {r[0]: (r[1], r[2]) for r in st}
    region = region_filter(
        st, pos[o_id], pos[d_id], cfg.region_ratio, cfg.region_margin_km
    )
    t0 = time.perf_counter()
    tt = load_timetable(con, date, feeds, region=region, cfg=cfg)
    timings["timetable_s"] = time.perf_counter() - t0
    log(f"timetable: {len(tt.trips)} trips, {len(tt.station_ids)} stations")
    net = Network(tt)
    net_regional = Network(regional_view(tt)) if regional else None
    origin, target = tt.index[o_id], tt.index[d_id]

    places = Places(pair.places or auto_places(tt, {origin, target}))
    exclude = {p for p in (places.of(o_name), places.of(d_name)) if p}
    exclude |= {city(o_name), city(d_name)}
    vias: list[int] = []
    for name in pair.search_via:
        try:
            sid, _ = find_station(con, name)
        except LookupError:
            log(f"  search_via {name!r}: no such station, skipped")
            continue
        if sid in tt.index and tt.index[sid] not in (origin, target):
            vias.append(tt.index[sid])

    directions = DIRECTIONS if both_directions else DIRECTIONS[:1]
    nets = {"all": net}
    if net_regional is not None:
        nets["regional"] = net_regional
    jobs: list[tuple[str, str, int, int, tuple[int, int], int, int | None]] = []
    for direction in directions:
        a, b = (origin, target) if direction == "outbound" else (target, origin)
        jobs.append((direction, "all", a, b, window, cfg.max_rounds, None))
        jobs += [(direction, "all", a, b, window, cfg.max_rounds, v) for v in vias]
        if net_regional is not None:
            jobs.append(
                (direction, "regional", a, b, window, cfg.max_rounds_regional, None)
            )
    t0 = time.perf_counter()
    found: dict[str, list[Journey]] = {}
    per_dir: dict[str, dict[tuple, Journey]] = {d: {} for d in directions}
    for direction, js in _run_all(nets, jobs):
        for j in js:
            per_dir[direction].setdefault(j.signature(), j)
    for direction in directions:
        found[direction] = sorted(
            per_dir[direction].values(), key=lambda j: (j.dep, j.arr)
        )
        log(f"{direction}: {len(found[direction])} candidate journeys")
    timings["search_s"] = time.perf_counter() - t0

    grouped = group(found, tt.names, places, exclude)
    chosen_corridors = select(
        grouped,
        ratio=cfg.corridor_ratio,
        slack_min=int(cfg.corridor_slack_hours * 60),
        pinned=tuple(e["name"] for e in pair.expected),
    )
    chosen = {
        d: pick_journeys(chosen_corridors, d, cfg.max_journeys) for d in directions
    }
    missing: dict[str, list[dict]] = {d: [] for d in directions}
    names = {c.name for c in chosen_corridors}
    for d in directions:
        present = {c.name for c, _ in chosen[d]}
        for exp in pair.expected:
            if exp["name"] not in present:
                reason = (
                    "found only in the other direction"
                    if exp["name"] in names
                    else "no journey found in the loaded feeds"
                )
                missing[d].append({**exp, "reason": reason})
        if not chosen[d]:
            missing[d].append(
                {"name": "any journey", "note": "", "reason": "no journey found"}
            )
    return Result(
        pair=pair,
        date=date,
        window=window,
        tt=tt,
        origin=origin,
        target=target,
        corridors=chosen_corridors,
        chosen=chosen,
        places=places,
        feeds=feeds,
        missing=missing,
        timings=timings,
    )


_NETS: dict[str, Network] = {}


def _job(job: tuple) -> tuple[str, list[Journey]]:
    direction, key, a, b, window, rounds, via = job
    return direction, _NETS[key].profile(a, b, *window, max_rounds=rounds, via=via)


def _run_all(
    nets: dict[str, Network], jobs: list[tuple]
) -> list[tuple[str, list[Journey]]]:
    """Run searches in forked worker processes (sequentially where fork is missing)."""
    _NETS.clear()
    _NETS.update(nets)
    workers = min(len(jobs), os.cpu_count() or 1)
    if workers <= 1 or "fork" not in mp.get_all_start_methods():
        return [_job(j) for j in jobs]
    ctx = mp.get_context("fork")  # workers inherit the networks without pickling
    with ctx.Pool(workers) as pool:
        return pool.map(_job, jobs, chunksize=1)


def parse_window(text: str) -> tuple[int, int]:
    """``04:00-11:00`` -> seconds."""
    a, b = text.split("-")

    def secs(t: str) -> int:
        h, m = t.strip().split(":")
        return int(h) * 3600 + int(m) * 60

    return secs(a), secs(b)
