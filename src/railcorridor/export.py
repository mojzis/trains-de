"""Turn an exploration result into the JSON document the site renders."""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path

import duckdb

from railcorridor.config import FEEDS
from railcorridor.corridors import (
    Corridor,
    dir_stats,
    hhmm,
    leg_station_names,
    simplify,
    visits,
)
from railcorridor.explore import Result, city
from railcorridor.fares import FareProvider
from railcorridor.router import Journey, Leg
from railcorridor.timetable import Timetable

SCHEMA_VERSION = 1


def feeds_meta(con: duckdb.DuckDBPyConnection, names: list[str]) -> list[dict]:
    """Feed name, validity, licence and attribution for the page footer."""
    rows = {
        r[0]: r
        for r in con.execute(
            "SELECT name, file, loaded_at, valid_from, valid_to FROM main.feeds"
        ).fetchall()
    }
    out = []
    for n in names:
        spec = FEEDS.get(n)
        r = rows.get(n)
        out.append(
            {
                "name": n,
                "title": spec.title if spec else n,
                "file": r[1] if r else None,
                "valid_from": r[3].isoformat() if r and r[3] else None,
                "valid_to": r[4].isoformat() if r and r[4] else None,
                "licence": spec.licence if spec else None,
                "attribution": spec.attribution if spec else None,
                "url": spec.attribution_url if spec else None,
            }
        )
    return out


def _leg(leg: Leg, tt: Timetable) -> dict:
    trip = leg.trip
    stops = trip.stations[leg.board : leg.alight + 1]
    parts = [p for p in trip.parts if p.first < leg.alight and p.last > leg.board]
    through = []
    if len(parts) > 1:
        for p in parts[1:]:
            at = trip.stations[max(p.first, leg.board)]
            through.append({"at": tt.station_ids[at], "label": p.label, "feed": p.feed})
    return {
        "from": tt.station_ids[leg.from_station],
        "to": tt.station_ids[leg.to_station],
        "dep": hhmm(leg.dep),
        "arr": hhmm(leg.arr),
        "dep_s": leg.dep,
        "arr_s": leg.arr,
        "label": trip.label,
        "category": trip.category,
        "route_type": trip.route_type,
        "long_distance": trip.long_distance,
        "operator": trip.agency,
        "feeds": sorted({p.feed for p in parts}) or [trip.feed],
        "stops": [tt.station_ids[s] for s in stops],
        "joined_at": through,
    }


def _journey(c: Corridor, j: Journey, tt: Timetable) -> dict:
    legs = [_leg(leg, tt) for leg in j.legs]
    notes = []
    for lg in legs:
        for jn in lg["joined_at"]:
            notes.append(
                f"{lg['label']} runs through {tt.names[tt.index[jn['at']]]}; the "
                "timetable data splits it there, treated here as one train."
            )
    if j.arr >= 86_400:
        notes.append("Arrives after midnight.")
    return {
        "corridor": c.id,
        "dep": hhmm(j.dep),
        "arr": hhmm(j.arr),
        "duration_min": (j.arr - j.dep) // 60,
        "changes": j.changes,
        "legs": legs,
        "notes": notes,
    }


def _via_text(c: Corridor, r: Result) -> str:
    js = c.journeys.get("outbound") or c.journeys.get("return") or []
    if not js:
        return ""
    j = js[0]
    per_leg = [leg_station_names(leg, r.tt.names) for leg in j.legs]
    if "outbound" not in c.journeys:
        per_leg = [list(reversed(x)) for x in reversed(per_leg)]
    vs = visits(per_leg, r.places, set())
    ends = {city(r.tt.names[r.origin]), city(r.tt.names[r.target])}
    return " · ".join(v.place for v in vs if v.place not in ends)


def _geometry(c: Corridor, r: Result) -> tuple[list[list[float]], list[str]]:
    """Simplified polyline through the stops of a representative journey."""
    tt = r.tt
    out_js = c.journeys.get("outbound")
    js = out_js or c.journeys.get("return") or []
    durs = sorted(js, key=lambda j: j.arr - j.dep)
    rep = durs[len(durs) // 2]
    seq: list[int] = []
    keep_st: set[int] = set()
    for leg in rep.legs:
        for s in leg.trip.stations[leg.board : leg.alight + 1]:
            if not seq or seq[-1] != s:
                seq.append(s)
        keep_st.add(leg.from_station)
        keep_st.add(leg.to_station)
    if not out_js:
        seq.reverse()
    for s in seq:
        if tt.hub[s] or r.places.of(tt.names[s]):
            keep_st.add(s)
    pts = [(tt.lat[s], tt.lon[s]) for s in seq]
    keep = {i for i, s in enumerate(seq) if s in keep_st}
    idx = simplify(pts, keep)
    geometry = [[round(pts[i][0], 4), round(pts[i][1], 4)] for i in idx]
    return geometry, [tt.station_ids[seq[i]] for i in idx]


def build_document(
    r: Result,
    con: duckdb.DuckDBPyConnection,
    fares: FareProvider,
    *,
    generated_at: dt.datetime | None = None,
) -> dict:
    """JSON-ready dict for one pair and date."""
    tt = r.tt
    window_h = (r.window[1] - r.window[0]) / 3600
    corridors = []
    used: set[str] = set()
    transfer_ids: set[str] = set()
    key_ids: set[str] = set()
    fastest = min(
        (
            statistics.median([j.arr - j.dep for j in js])
            for c in r.corridors
            for js in c.journeys.values()
            if js
        ),
        default=None,
    )
    for c in r.corridors:
        geometry, path = _geometry(c, r)
        used.update(path)
        key_ids.update(path)
        fare = fares.fare(c.id, regional=c.regional, date=r.date)
        stats = {
            d: dir_stats(js).as_dict(window_h) for d, js in c.journeys.items() if js
        }
        tags = []
        if c.regional:
            tags.append("Regional trains only")
        med = [
            statistics.median([j.arr - j.dep for j in js])
            for js in c.journeys.values()
            if js
        ]
        if fastest is not None and med and min(med) == fastest:
            tags.append("Fastest")
        if c.fewest_changes() <= 1:
            tags.append("1 change" if c.fewest_changes() == 1 else "Direct")
        corridors.append(
            {
                "id": c.id,
                "name": c.name,
                "regional": c.regional,
                "color": c.color[0],
                "color_dark": c.color[1],
                "via": _via_text(c, r),
                "geometry": geometry,
                "path": path,
                "stats": stats,
                "tags": tags,
                "fare": (
                    {"summary": fare.summary, "note": fare.note, "source": fare.source}
                    if fare
                    else None
                ),
            }
        )

    journeys: dict[str, list[dict]] = {}
    for d, pairs in r.chosen.items():
        journeys[d] = []
        for c, j in pairs:
            doc = _journey(c, j, tt)
            journeys[d].append(doc)
            for lg in doc["legs"]:
                used.update(lg["stops"])
            for lg in doc["legs"][1:]:
                transfer_ids.add(lg["from"])

    ends = {tt.station_ids[r.origin], tt.station_ids[r.target]}
    stations = {}
    for sid in sorted(used):
        s = tt.index[sid]
        kind = "minor"
        if sid in ends:
            kind = "end"
        elif sid in transfer_ids or (
            sid in key_ids and (tt.hub[s] or r.places.of(tt.names[s]))
        ):
            kind = "hub"
        stations[sid] = {
            "name": tt.names[s],
            "lat": round(tt.lat[s], 5),
            "lon": round(tt.lon[s], 5),
            "hub": kind != "minor",
            "kind": kind,
            "on_map": sid in key_ids or sid in transfer_ids or sid in ends,
        }

    notices = []
    feeds = feeds_meta(con, r.feeds)
    for f in feeds:
        lo, hi = f["valid_from"], f["valid_to"]
        if lo and hi and not (lo <= r.date.isoformat() <= hi):
            notices.append(
                f"{f['title']} is valid {lo} to {hi}; {r.date.isoformat()} is outside "
                "that window, so its trains are missing."
            )

    return {
        "schema": SCHEMA_VERSION,
        "pair": {
            "id": r.pair.slug,
            "from": tt.names[r.origin],
            "to": tt.names[r.target],
            "from_id": tt.station_ids[r.origin],
            "to_id": tt.station_ids[r.target],
            "title": r.pair.title
            or f"{city(tt.names[r.origin])} ⇄ {city(tt.names[r.target])}",
            "subtitle": r.pair.subtitle,
        },
        "date": r.date.isoformat(),
        "weekday": r.date.strftime("%A"),
        "window": [hhmm(r.window[0]), hhmm(r.window[1])],
        "generated_at": (generated_at or dt.datetime.now(dt.UTC)).isoformat(
            timespec="seconds"
        ),
        "feeds": feeds,
        "notices": notices,
        "missing": r.missing,
        "stations": stations,
        "corridors": corridors,
        "fare_footnote": fares.footnote(),
        "journeys": journeys,
    }


def write_json(doc: dict, out_dir: Path) -> Path:
    """Write ``out/<pair>/<date>.json``."""
    path = out_dir / doc["pair"]["id"] / f"{doc['date']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
