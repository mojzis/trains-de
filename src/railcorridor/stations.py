"""Merge stops from all feeds into one ``stations`` table.

Stops are first collapsed onto their ``parent_station``. Candidates from every
feed are then united when they lie within ``MERGE_RADIUS_M`` of each other and
their normalised names are compatible (same first word, or one name's words
contained in the other's). The result is ``main.stations`` plus
``main.stop_station`` mapping every (feed, stop_id) to a station.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

import duckdb

from railcorridor.config import HUB_NAMES, MERGE_RADIUS_M

_EXPAND = [
    (r"\bhl\s*n\b", "hlavni nadrazi"),
    (r"\bhl\s*nadr\b", "hlavni nadrazi"),
    (r"\bhbf\b", "hauptbahnhof"),
    (r"\bhauptbf\b", "hauptbahnhof"),
    (r"\bbf\b", "bahnhof"),
    (r"\bst\b", "station"),
]
# Words that never distinguish one station from another at the same place.
_GENERIC = {
    "bahnhof",
    "station",
    "nadrazi",
    "hauptbahnhof",
    "hlavni",
    "gleis",
    "position",
    "tief",
    "oben",
    "unten",
    "s",
    "u",
    "gr",
}


def fold(text: str) -> str:
    """Lowercase ASCII fold: ``Děčín`` -> ``decin``, ``Lüneburg`` -> ``luneburg``."""
    text = text.replace("ß", "ss")
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def normalise(name: str) -> str:
    """Normalise a station name for comparison."""
    s = fold(name)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for pat, rep in _EXPAND:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(name: str) -> list[str]:
    """Significant words of a normalised name."""
    return [t for t in normalise(name).split() if t not in _GENERIC]


def names_compatible(a: str, b: str) -> bool:
    """Whether two stop names can denote the same station."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return normalise(a) == normalise(b)
    if ta[0] == tb[0]:
        return True
    sa, sb = set(ta), set(tb)
    return sa <= sb or sb <= sa


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def slug(text: str) -> str:
    """URL/id-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


@dataclass
class Candidate:
    """A station-level stop of one feed (parent station or parentless stop)."""

    feed: str
    stop_id: str
    name: str
    lat: float
    lon: float
    weight: int  # number of stop events
    names: dict[str, int] = field(default_factory=dict)  # all names, weighted


class _DSU:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def merge_candidates(
    cands: list[Candidate], radius_m: float = MERGE_RADIUS_M
) -> list[int]:
    """Group index per candidate: near-by candidates with compatible names merge."""
    dsu = _DSU(len(cands))
    cell = radius_m / 111_000.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, c in enumerate(cands):
        grid[(int(c.lat // cell), int(c.lon // cell))].append(i)
    for i, c in enumerate(cands):
        gy, gx = int(c.lat // cell), int(c.lon // cell)
        for dy in (-1, 0, 1):
            for dx in (-2, -1, 0, 1, 2):  # a lon degree is shorter than lat
                for j in grid.get((gy + dy, gx + dx), ()):
                    if j <= i:
                        continue
                    o = cands[j]
                    if haversine_m(
                        c.lat, c.lon, o.lat, o.lon
                    ) <= radius_m and names_compatible(c.name, o.name):
                        dsu.union(i, j)
    return [dsu.find(i) for i in range(len(cands))]


def display_name(names: list[tuple[str, int]]) -> str:
    """Pick a display name.

    Plain names win over bus-stop style ones (``Hamburg, HBF/Kirchenallee``),
    then names with diacritics (``Děčín`` over ``Decin``), then the shortest.
    """

    def key(item: tuple[str, int]) -> tuple[int, int, int, int]:
        n, w = item
        messy = any(ch in n for ch in ",/+()") or n.isupper()
        has_diacritics = any(ord(ch) > 127 for ch in n)
        return (int(messy), 0 if has_diacritics else 1, len(n), -w)

    return sorted(names, key=key)[0][0]


def is_hub_name(name: str, hub_names: tuple[str, ...] = HUB_NAMES) -> bool:
    """Whether ``name`` denotes one of the configured hub stations."""
    norm = normalise(name)
    if "hauptbahnhof" not in norm and "hlavni nadrazi" not in norm:
        return False
    return any(tokens(name) == tokens(h) for h in hub_names)


def _candidates(
    con: duckdb.DuckDBPyConnection, feed: str
) -> tuple[list[Candidate], dict[str, str]]:
    """Station-level candidates of one feed plus stop_id -> candidate stop_id."""
    rows = con.execute(
        f"""WITH ev AS (SELECT stop_id, count(*) AS n FROM {feed}.stop_times
                        GROUP BY stop_id),
            s AS (SELECT s.*, coalesce(p.stop_id, s.stop_id) AS root,
                         coalesce(p.stop_name, s.stop_name) AS root_name,
                         coalesce(p.stop_lat, s.stop_lat) AS root_lat,
                         coalesce(p.stop_lon, s.stop_lon) AS root_lon
                  FROM {feed}.stops s
                  LEFT JOIN {feed}.stops p ON p.stop_id = s.parent_station)
            SELECT s.stop_id, s.root, s.root_name, s.root_lat, s.root_lon,
                   coalesce(ev.n, 0), s.stop_name
            FROM s LEFT JOIN ev USING (stop_id)
            WHERE s.root_lat IS NOT NULL"""
    ).fetchall()
    to_root: dict[str, str] = {}
    agg: dict[str, Candidate] = {}
    for stop_id, root, name, lat, lon, n, own_name in rows:
        to_root[stop_id] = root
        c = agg.get(root)
        if c is None:
            c = agg[root] = Candidate(feed, root, name, lat, lon, 0)
        c.weight += n
        c.names[name] = c.names.get(name, 0) + n
        if own_name:
            c.names[own_name] = c.names.get(own_name, 0) + n
    return list(agg.values()), to_root


def build_stations(con: duckdb.DuckDBPyConnection, feeds: list[str]) -> int:
    """(Re)build ``main.stations`` and ``main.stop_station``; returns station count."""
    cands: list[Candidate] = []
    to_root: dict[tuple[str, str], str] = {}
    for f in feeds:
        cs, roots = _candidates(con, f)
        cands.extend(cs)
        to_root.update({(f, s): r for s, r in roots.items()})
    groups = merge_candidates(cands)

    members: dict[int, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        members[g].append(i)

    used_ids: set[str] = set()
    station_of_cand: dict[tuple[str, str], str] = {}
    station_rows = []
    for g in sorted(members):
        idx = members[g]
        name = display_name([nw for i in idx for nw in cands[i].names.items()])
        sid = slug(name) or f"st-{g}"
        base, k = sid, 2
        while sid in used_ids:
            sid, k = f"{base}-{k}", k + 1
        used_ids.add(sid)
        w = sum(cands[i].weight for i in idx) or 1
        lat = sum(cands[i].lat * max(cands[i].weight, 1) for i in idx) / sum(
            max(cands[i].weight, 1) for i in idx
        )
        lon = sum(cands[i].lon * max(cands[i].weight, 1) for i in idx) / sum(
            max(cands[i].weight, 1) for i in idx
        )
        is_hub = any(is_hub_name(n) for i in idx for n in cands[i].names)
        feeds_of = sorted({cands[i].feed for i in idx})
        station_rows.append((sid, name, lat, lon, is_hub, w, ",".join(feeds_of)))
        for i in idx:
            station_of_cand[(cands[i].feed, cands[i].stop_id)] = sid

    con.execute("DROP TABLE IF EXISTS main.stations")
    con.execute(
        """CREATE TABLE main.stations (station_id VARCHAR PRIMARY KEY, name VARCHAR,
           lat DOUBLE, lon DOUBLE, hub BOOLEAN, events INT, feeds VARCHAR)"""
    )
    con.executemany(
        "INSERT INTO main.stations VALUES (?, ?, ?, ?, ?, ?, ?)", station_rows
    )
    con.execute("DROP TABLE IF EXISTS main.stop_station")
    con.execute(
        """CREATE TABLE main.stop_station (feed VARCHAR, stop_id VARCHAR,
           station_id VARCHAR, PRIMARY KEY (feed, stop_id))"""
    )
    rows = [
        (f, s, station_of_cand[(f, r)])
        for (f, s), r in to_root.items()
        if (f, r) in station_of_cand
    ]
    con.executemany("INSERT INTO main.stop_station VALUES (?, ?, ?)", rows)
    return len(station_rows)


def find_station(con: duckdb.DuckDBPyConnection, query: str) -> tuple[str, str]:
    """Resolve a user-typed station name to (station_id, name)."""
    rows = con.execute(
        "SELECT station_id, name, hub, events FROM main.stations"
    ).fetchall()
    q = normalise(query)
    qt = tokens(query)
    exact = [r for r in rows if normalise(r[1]) == q]
    if not exact:
        exact = [r for r in rows if tokens(r[1]) == qt]
    if not exact:
        exact = [r for r in rows if normalise(r[1]).startswith(q)]
    if not exact:
        exact = [r for r in rows if qt and set(qt) <= set(tokens(r[1]))]
    if not exact:
        msg = f"no station matches {query!r}"
        raise LookupError(msg)
    best = sorted(exact, key=lambda r: (not r[2], -r[3], len(r[1])))[0]
    return best[0], best[1]
