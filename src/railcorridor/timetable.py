"""Materialise one service day of rail trips at station level.

Times are seconds after midnight of the query date; trips of the previous
service day that run past 24:00 are included, shifted by -24 h.

Feeds sometimes cut one physical train into several trips (at a border, or
where the feed changes). ``join_through_trips`` glues such fragments back
together so riding through does not count as a change.
"""

from __future__ import annotations

import datetime as dt
import itertools
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

import duckdb

from railcorridor.config import RoutingConfig
from railcorridor.labels import derive_label
from railcorridor.load import ident
from railcorridor.stations import haversine_m

DAY = 86_400
_DUP_TOLERANCE_S = 300


@dataclass
class Part:
    """A source trip (fragment) that makes up part of a timetable trip."""

    feed: str
    trip_id: str
    label: str
    first: int  # index into the joined trip's stop list
    last: int


@dataclass
class Trip:
    """One vehicle run on the query day, at station level."""

    key: str
    feed: str
    route_id: str
    agency: str
    label: str
    category: str
    long_distance: bool
    label_quality: int
    route_type: int
    stations: list[int]
    arr: list[int]
    dep: list[int]
    board: list[bool]
    alight: list[bool]
    parts: list[Part] = field(default_factory=list)


@dataclass
class Timetable:
    """Stations and trips of one day."""

    date: dt.date
    station_ids: list[str]
    names: list[str]
    lat: list[float]
    lon: list[float]
    hub: list[bool]
    change_s: list[int]
    no_transfer: set[int]
    trips: list[Trip]
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Build the station id -> index map."""
        self.index = {sid: i for i, sid in enumerate(self.station_ids)}


def active_services_sql(schema: str) -> str:
    """SQL (params: date, date) giving (service_id, off) active on date/date-1."""
    return f"""
    WITH days(d, off) AS (SELECT ?::DATE, 0 UNION ALL SELECT ?::DATE - 1, {DAY})
    SELECT c.service_id, days.off FROM {schema}.calendar c, days
      WHERE days.d BETWEEN c.start_date AND c.end_date
        AND (CASE isodow(days.d) WHEN 1 THEN c.monday WHEN 2 THEN c.tuesday
             WHEN 3 THEN c.wednesday WHEN 4 THEN c.thursday WHEN 5 THEN c.friday
             WHEN 6 THEN c.saturday ELSE c.sunday END) = 1
    UNION
    SELECT cd.service_id, days.off FROM {schema}.calendar_dates cd
      JOIN days ON cd.date = days.d WHERE cd.exception_type = 1
    EXCEPT
    SELECT cd.service_id, days.off FROM {schema}.calendar_dates cd
      JOIN days ON cd.date = days.d WHERE cd.exception_type = 2
    """


def region_filter(
    stations: list[tuple[str, float, float]],
    a: tuple[float, float],
    b: tuple[float, float],
    ratio: float,
    margin_km: float,
) -> set[str]:
    """Station ids inside the ellipse d(a,s)+d(s,b) <= ratio*d(a,b)+margin."""
    base = haversine_m(*a, *b)
    limit = ratio * base + margin_km * 1000
    return {
        sid
        for sid, lat, lon in stations
        if haversine_m(*a, lat, lon) + haversine_m(lat, lon, *b) <= limit
    }


def load_timetable(
    con: duckdb.DuckDBPyConnection,
    date: dt.date,
    feeds: list[str],
    *,
    region: set[str] | None = None,
    regional_only: bool = False,
    cfg: RoutingConfig | None = None,
) -> Timetable:
    """Build the station-level timetable for ``date``."""
    cfg = cfg or RoutingConfig()
    st_rows = con.execute(
        "SELECT station_id, name, lat, lon, hub FROM main.stations ORDER BY station_id"
    ).fetchall()
    extra = set(cfg.extra_hubs)
    if region is not None:
        st_rows = [r for r in st_rows if r[0] in region]
    ids = [r[0] for r in st_rows]
    idx = {sid: i for i, sid in enumerate(ids)}
    hub = [bool(r[4]) or r[1] in extra for r in st_rows]
    change = [(cfg.hub_change_min if h else cfg.default_change_min) * 60 for h in hub]
    no_transfer = {i for i, r in enumerate(st_rows) if "(Gr)" in r[1]}

    trips: list[Trip] = []
    for f in map(ident, feeds):
        trips.extend(_feed_trips(con, f, date, idx))
        _apply_transfers(con, f, idx, change)
    trips = join_through_trips(
        trips, cfg.through_join_max_gap_s, near_border(st_rows, cfg.border_radius_km)
    )
    if regional_only:
        trips = [t for t in trips if not t.long_distance]
    return Timetable(
        date=date,
        station_ids=ids,
        names=[r[1] for r in st_rows],
        lat=[r[2] for r in st_rows],
        lon=[r[3] for r in st_rows],
        hub=hub,
        change_s=change,
        no_transfer=no_transfer,
        trips=trips,
    )


def _feed_trips(
    con: duckdb.DuckDBPyConnection, f: str, date: dt.date, idx: dict[str, int]
) -> list[Trip]:
    sql = f"""
    WITH act AS ({active_services_sql(f)}),
    late AS (SELECT DISTINCT trip_id FROM {f}.stop_times WHERE arr >= {DAY}),
    tr AS (
      SELECT t.trip_id, act.off, t.route_id, t.trip_short_name
      FROM {f}.trips t JOIN act USING (service_id)
      WHERE act.off = 0 OR t.trip_id IN (SELECT trip_id FROM late))
    SELECT tr.trip_id, tr.off, r.route_id, r.route_short_name, r.route_long_name,
           r.route_type, a.agency_name, tr.trip_short_name, ss.station_id,
           st.arr - tr.off, st.dep - tr.off, st.pickup_type, st.drop_off_type
    FROM tr JOIN {f}.routes r USING (route_id)
    LEFT JOIN {f}.agency a ON a.agency_id = r.agency_id
    JOIN {f}.stop_times st ON st.trip_id = tr.trip_id
    JOIN main.stop_station ss ON ss.feed = '{f}' AND ss.stop_id = st.stop_id
    ORDER BY tr.trip_id, tr.off, st.stop_sequence
    """
    rows = con.execute(sql, [date, date]).fetchall()
    out: list[Trip] = []
    cur_key = None
    buf: list[tuple] = []
    for row in rows:
        key = (row[0], row[1])
        if key != cur_key:
            if buf:
                t = _make_trip(f, buf, idx)
                if t:
                    out.append(t)
            buf, cur_key = [], key
        buf.append(row)
    if buf:
        t = _make_trip(f, buf, idx)
        if t:
            out.append(t)
    return out


def _make_trip(f: str, rows: list[tuple], idx: dict[str, int]) -> Trip | None:
    trip_id, off, route_id, rsn, rln, rtype, agency, tsn = rows[0][:8]
    stations: list[int] = []
    arr: list[int] = []
    dep: list[int] = []
    board: list[bool] = []
    alight: list[bool] = []
    for r in rows:
        s = idx.get(r[8])
        if s is None:
            continue  # outside the region
        a, d, pick, drop = r[9], r[10], r[11], r[12]
        if stations and stations[-1] == s:
            # consecutive stops merged into one station: keep first arrival
            dep[-1] = d
            board[-1] = board[-1] or pick != 1
            continue
        stations.append(s)
        arr.append(a)
        dep.append(d)
        board.append(pick != 1)
        alight.append(drop != 1)
    if len(stations) < 2 or dep[-1] < 0:
        return None
    lab = derive_label(rsn, rln, tsn, agency, rtype)
    key = f"{f}:{trip_id}" + ("@-1" if off else "")
    return Trip(
        key=key,
        feed=f,
        route_id=route_id,
        agency=agency or "",
        label=lab.text,
        category=lab.category,
        long_distance=lab.long_distance,
        label_quality=lab.quality,
        route_type=rtype,
        stations=stations,
        arr=arr,
        dep=dep,
        board=board,
        alight=alight,
        parts=[Part(f, trip_id, lab.text, 0, len(stations) - 1)],
    )


def _apply_transfers(
    con: duckdb.DuckDBPyConnection, f: str, idx: dict[str, int], change: list[int]
) -> None:
    """Use transfers.txt min_transfer_time for same-station, trip-agnostic rows."""
    rows = con.execute(
        f"""SELECT a.station_id, t.min_transfer_time FROM {f}.transfers t
            JOIN main.stop_station a ON a.feed = '{f}' AND a.stop_id = t.from_stop_id
            JOIN main.stop_station b ON b.feed = '{f}' AND b.stop_id = t.to_stop_id
            WHERE a.station_id = b.station_id AND t.transfer_type = 2
              AND t.min_transfer_time IS NOT NULL AND t.from_trip_id IS NULL
              AND t.to_trip_id IS NULL AND t.from_route_id IS NULL
              AND t.to_route_id IS NULL"""
    ).fetchall()
    per: dict[int, list[int]] = defaultdict(list)
    for sid, secs in rows:
        s = idx.get(sid)
        if s is not None:
            per[s].append(secs)
    for s, vals in per.items():
        change[s] = max(60, min(1800, int(statistics.median(vals))))


def near_border(st_rows: list[tuple], radius_km: float) -> set[int]:
    """Indices of stations within ``radius_km`` of a border point ``(Gr)``."""
    borders = [(r[2], r[3]) for r in st_rows if "(Gr)" in r[1]]
    return {
        i
        for i, r in enumerate(st_rows)
        if any(
            haversine_m(r[2], r[3], la, lo) <= radius_km * 1000 for la, lo in borders
        )
    }


def join_through_trips(
    trips: list[Trip], max_gap_s: int = 600, near_border: set[int] | None = None
) -> list[Trip]:
    """Glue fragments of one physical train into a single trip.

    A continues into B when A's last station is served by B (at B's start, or
    mid-way for a copy of the same train in another feed) and B leaves there
    between 3 min before and ``max_gap_s`` after A arrives. Turnarounds (B
    heading back where A came from) never join, and the two must look like
    one train (see ``_same_train``). ``near_border`` holds station indices
    close to a border point, where operators hand trains over.
    """
    near_border = near_border or set()
    by_station: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for bi, b in enumerate(trips):
        for j, s in enumerate(b.stations[:-1]):
            by_station[s].append((bi, j))

    best: dict[int, tuple[int, int, int]] = {}  # A -> (|gap|, B, j)
    for ai, a in enumerate(trips):
        s, t = a.stations[-1], a.arr[-1]
        prev = a.stations[-2]
        for bi, j in by_station.get(s, ()):
            if bi == ai:
                continue
            b = trips[bi]
            gap = b.dep[j] - t
            if gap < -180 or gap > max_gap_s:
                continue
            if b.stations[j + 1] == prev:
                continue  # turnaround
            if not _same_train(a, b, j, near_border=s in near_border):
                continue
            cand = (abs(gap), bi, j)
            if ai not in best or cand < best[ai]:
                best[ai] = cand

    # each B accepts only its closest predecessor
    pred: dict[int, tuple[int, int]] = {}
    for ai, (g, bi, _j) in sorted(best.items(), key=lambda kv: kv[1][0]):
        if bi not in pred:
            pred[bi] = (g, ai)
    succ = {ai: (best[ai][1], best[ai][2]) for _, ai in pred.values()}
    has_pred = set(pred)

    out: list[Trip] = []
    seen: set[int] = set()
    for start in range(len(trips)):
        if start in has_pred or start in seen:
            continue
        chain = [start]
        seen.add(start)
        while chain[-1] in succ and succ[chain[-1]][0] not in seen:
            nxt = succ[chain[-1]][0]
            chain.append(nxt)
            seen.add(nxt)
        out.append(_concat(trips, chain, succ))
    # cycles (no start) are left as they were
    out.extend(t for i, t in enumerate(trips) if i not in seen)
    return out


def _same_train(a: Trip, b: Trip, j: int, *, near_border: bool = False) -> bool:
    """Whether B (entered at its stop ``j``) can be the continuation of A."""
    same_feed = a.feed == b.feed
    if j > 0:
        # B is a copy of the same train from another feed: it must also call
        # at one of A's earlier stations at about the same time
        if same_feed or not (
            a.label == b.label or (near_border and a.long_distance and b.long_distance)
        ):
            return False
        a_times = dict(zip(a.stations[:-1], a.dep[:-1], strict=True))
        return any(
            s in a_times and abs(a_times[s] - d) <= _DUP_TOLERANCE_S
            for s, d in zip(b.stations[:j], b.dep[:j], strict=True)
        )
    if a.label == b.label or (same_feed and a.route_id == b.route_id):
        return True
    # e.g. a Czech RJ fragment ending at Děčín, continued as DB line 27
    return near_border and a.long_distance and b.long_distance and a.agency != b.agency


def _concat(
    trips: list[Trip], chain: list[int], succ: dict[int, tuple[int, int]]
) -> Trip:
    if len(chain) == 1:
        return trips[chain[0]]
    first = trips[chain[0]]
    stations = list(first.stations)
    arr, dep = list(first.arr), list(first.dep)
    board, alight = list(first.board), list(first.alight)
    parts = [Part(p.feed, p.trip_id, p.label, p.first, p.last) for p in first.parts]
    for a_i, b_i in itertools.pairwise(chain):
        b = trips[b_i]
        j = succ[a_i][1]
        join_at = len(stations) - 1
        dep[-1] = max(b.dep[j], arr[-1])
        board[-1] = board[-1] or b.board[j]
        for p in b.parts:
            if p.last <= j:
                continue
            parts.append(
                Part(
                    p.feed,
                    p.trip_id,
                    p.label,
                    join_at + max(p.first - j, 0),
                    join_at + p.last - j,
                )
            )
        stations.extend(b.stations[j + 1 :])
        arr.extend(b.arr[j + 1 :])
        dep.extend(b.dep[j + 1 :])
        board.extend(b.board[j + 1 :])
        alight.extend(b.alight[j + 1 :])
    members = [trips[i] for i in chain]
    lead = max(members, key=lambda t: (t.label_quality, t.long_distance))
    return Trip(
        key="+".join(t.key for t in members),
        feed=first.feed,
        route_id=first.route_id,
        agency=lead.agency,
        label=lead.label,
        category=lead.category,
        long_distance=any(t.long_distance for t in members),
        label_quality=lead.label_quality,
        route_type=lead.route_type,
        stations=stations,
        arr=arr,
        dep=dep,
        board=board,
        alight=alight,
        parts=parts,
    )
