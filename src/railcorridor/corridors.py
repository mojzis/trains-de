"""Group journeys into corridors ("painted routes"), name them and summarise them.

A journey is reduced to the *places* it passes (stations mapped to configured
place names such as Berlin or Nürnberg) and where it changes trains. The
corridor name is ``A → T``: T is the last place where the journey changes, A
the most distinctive place among the previous change point and the places
passed between it and T. Examples: ``Berlin → Hamburg`` (ComfortJet, change in
Hamburg), ``Leipzig → Hannover``, ``Magdeburg → Uelzen (regional)``.

Names are always given from the outbound point of view, so a return journey is
reversed before naming and lands in the same corridor.
"""

from __future__ import annotations

import itertools
import math
import re
import statistics
from dataclasses import dataclass, field

from railcorridor.router import Journey, Leg, pareto
from railcorridor.stations import slug, tokens

# Palette (light, dark): the prototype's blue, red and purple, then extras.
# Green is reserved for the regional-only corridor, as in the prototype.
PALETTE = [
    ("#1F5FBF", "#6A9DEB"),
    ("#C0392B", "#EA6F63"),
    ("#7A4FB5", "#A987DE"),
    ("#B7791F", "#E3A94F"),
    ("#1B8A99", "#4FC3D1"),
    ("#A3456E", "#D97CA5"),
    ("#5B6B2F", "#9DB15C"),
]
REGIONAL_COLOR = ("#2F8A57", "#52BA82")


@dataclass(frozen=True)
class Visit:
    """A place on a journey's path and whether the traveller changes there."""

    place: str
    transfer: bool


@dataclass
class Places:
    """Ordered place list; earlier = more distinctive when naming."""

    names: list[str]

    def rank(self, place: str) -> int:
        """Lower is more distinctive; ad-hoc places come last."""
        try:
            return self.names.index(place)
        except ValueError:
            return len(self.names)

    def of(self, station_name: str) -> str | None:
        """The place a station belongs to, if any."""
        toks = tokens(station_name)
        if not toks:
            return None
        for p in self.names:
            ptoks = tokens(p)
            if ptoks and toks[: len(ptoks)] == ptoks:
                return p
        return None


def visits(
    stations_per_leg: list[list[str]], places: Places, exclude: set[str]
) -> list[Visit]:
    """Places passed by a journey given the station names of each leg's stops.

    ``exclude`` holds the origin/destination places, which never name a
    corridor. Consecutive repeats are collapsed; a place is a transfer if a leg
    ends there (other than the last leg). A change at a station outside the
    place list still counts, under the station's own name.
    """
    out: list[Visit] = []
    n = len(stations_per_leg)
    for li, names in enumerate(stations_per_leg):
        for si, nm in enumerate(names):
            is_transfer = si == len(names) - 1 and li < n - 1
            p = places.of(nm) or (_adhoc(nm) if is_transfer else None)
            if p is None or p in exclude:
                continue
            if out and out[-1].place == p:
                if is_transfer and not out[-1].transfer:
                    out[-1] = Visit(p, True)
                continue
            out.append(Visit(p, is_transfer))
    return out


def _adhoc(station_name: str) -> str:
    """Place name for a change station outside the list: ``Büchen``."""
    return re.sub(r"\s*(\(.*\)|Hbf|hl\.n\.|hlavní nádraží)\s*$", "", station_name)


def corridor_name(vs: list[Visit], places: Places, *, regional: bool) -> str | None:
    """Human name for a corridor, or None if the journey touches no listed place."""
    if not any(v.place in places.names for v in vs):
        return None
    transfers = [i for i, v in enumerate(vs) if v.transfer]
    suffix = " (regional)" if regional else ""
    if not transfers:
        best = min(vs, key=lambda v: places.rank(v.place))
        return f"via {best.place}{suffix}"
    t = transfers[-1]
    prev = transfers[-2] if len(transfers) > 1 else -1
    # the previous change point competes with the places passed since then
    cands = vs[max(prev, 0) : t] if prev >= 0 else vs[:t]
    if not cands:
        return f"{vs[t].place}{suffix}"
    a = min(cands, key=lambda v: places.rank(v.place)).place
    return f"{a} → {vs[t].place}{suffix}"


def is_regional(j: Journey) -> bool:
    """True if no leg needs a long-distance ticket."""
    return not any(leg.trip.long_distance for leg in j.legs)


def main_trip_key(j: Journey) -> str:
    """Key of the longest leg's trip: journeys sharing it are variants."""
    longest = max(j.legs, key=lambda leg: leg.arr - leg.dep)
    return longest.trip.key


def collapse(journeys: list[Journey]) -> list[Journey]:
    """Keep one journey per main train: fewest changes, then latest departure."""
    best: dict[str, Journey] = {}
    for j in journeys:
        k = main_trip_key(j)
        o = best.get(k)
        if o is None or (j.changes, -j.dep, j.arr, -j.min_slack) < (
            o.changes,
            -o.dep,
            o.arr,
            -o.min_slack,
        ):
            best[k] = j
    return sorted(best.values(), key=lambda j: (j.dep, j.arr))


def trim(
    journeys: list[Journey],
    *,
    ratio: float = 1.3,
    slack_s: int = 1800,
    extra_changes: int = 2,
) -> list[Journey]:
    """Drop journeys much slower, or with many more changes, than the best one."""
    if not journeys:
        return []
    fastest = min(j.arr - j.dep for j in journeys)
    fewest = min(j.changes for j in journeys)
    limit = max(fastest * ratio, fastest + slack_s)
    return [
        j
        for j in journeys
        if j.arr - j.dep <= limit and j.changes <= fewest + extra_changes
    ]


@dataclass
class DirStats:
    """Per-direction summary of a corridor."""

    count: int
    median_min: int
    min_min: int
    changes_min: int
    changes_max: int
    first_dep: int
    last_arr: int

    def as_dict(self, window_h: float) -> dict:
        """JSON form."""
        per = window_h / self.count if self.count else None
        return {
            "journeys": self.count,
            "duration_median_min": self.median_min,
            "duration_min_min": self.min_min,
            "changes_min": self.changes_min,
            "changes_max": self.changes_max,
            "earliest_dep": hhmm(self.first_dep),
            "latest_arr": hhmm(self.last_arr),
            "every_h": round(per, 1) if per else None,
        }


def dir_stats(js: list[Journey]) -> DirStats:
    """Median duration, change counts, frequency and span of some journeys."""
    durs = [(j.arr - j.dep) // 60 for j in js]
    return DirStats(
        count=len(js),
        median_min=int(statistics.median(durs)),
        min_min=min(durs),
        changes_min=min(j.changes for j in js),
        changes_max=max(j.changes for j in js),
        first_dep=min(j.dep for j in js),
        last_arr=max(j.arr for j in js),
    )


def hhmm(secs: int) -> str:
    """``HH:MM``; times past midnight keep counting (``25:10``)."""
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}"


@dataclass
class Corridor:
    """A named group of journeys sharing a route."""

    id: str
    name: str
    regional: bool
    journeys: dict[str, list[Journey]] = field(default_factory=dict)
    color: tuple[str, str] = PALETTE[0]

    def best_minutes(self) -> int:
        """Fastest journey in any direction."""
        return min((j.arr - j.dep) // 60 for js in self.journeys.values() for j in js)

    def fewest_changes(self) -> int:
        """Fewest changes in any direction."""
        return min(j.changes for js in self.journeys.values() for j in js)


def leg_station_names(leg: Leg, names: list[str]) -> list[str]:
    """Names of every stop on a leg, boarding to alighting."""
    return [names[s] for s in leg.trip.stations[leg.board : leg.alight + 1]]


def group(
    by_direction: dict[str, list[Journey]],
    station_names: list[str],
    places: Places,
    exclude: set[str],
) -> dict[str, Corridor]:
    """Assign journeys to corridors. Return journeys are named reversed."""
    out: dict[str, Corridor] = {}
    for direction, js in by_direction.items():
        for j in js:
            per_leg = [leg_station_names(leg, station_names) for leg in j.legs]
            if direction != "outbound":
                per_leg = [list(reversed(x)) for x in reversed(per_leg)]
            regional = is_regional(j)
            name = corridor_name(
                visits(per_leg, places, exclude), places, regional=regional
            )
            if name is None:
                continue
            cid = slug(name)
            c = out.setdefault(cid, Corridor(cid, name, regional))
            c.journeys.setdefault(direction, []).append(j)
    return out


def drop_clearly_worse(corridors: dict[str, Corridor], margin_s: int) -> None:
    """Drop non-regional journeys that another one beats by a wide margin.

    J goes when some other non-regional journey leaves no earlier, has no more
    changes and arrives at least ``margin_s`` sooner. Plain Pareto dominance
    would also remove useful fallbacks (another corridor a few minutes
    slower); the margin keeps those. Regional journeys are compared only
    among themselves by the per-corridor Pareto step.
    """
    by_dir: dict[str, list[Journey]] = {}
    for c in corridors.values():
        if c.regional:
            continue
        for d, js in c.journeys.items():
            by_dir.setdefault(d, []).extend(js)
    for c in corridors.values():
        if c.regional:
            continue
        for d, js in c.journeys.items():
            others = by_dir.get(d, [])
            c.journeys[d] = [
                j
                for j in js
                if not any(
                    k.dep >= j.dep
                    and k.changes <= j.changes
                    and k.arr <= j.arr - margin_s
                    for k in others
                )
            ]


def select(
    corridors: dict[str, Corridor],
    *,
    ratio: float,
    slack_min: int,
    max_changes: int = 4,
    max_corridors: int = 4,
    max_regional: int = 1,
    pinned: tuple[str, ...] = (),
    margin_min: int = 90,
) -> list[Corridor]:
    """Drop implausible corridors, order the rest, assign colours.

    Corridors whose name is in ``pinned`` (the pair's expected corridors) are
    kept whenever they were found and come first, in that order; the
    remaining slots go to the fastest.
    """
    for c in corridors.values():
        for d, js in c.journeys.items():
            c.journeys[d] = trim(collapse(pareto(js)))
    drop_clearly_worse(corridors, margin_s=margin_min * 60)
    corridors = {k: c for k, c in corridors.items() if any(c.journeys.values())}
    normal = [c for c in corridors.values() if not c.regional]
    regional = [c for c in corridors.values() if c.regional]
    if normal:
        best = min(c.best_minutes() for c in normal)
        limit = max(best * ratio, best + slack_min)
        normal = [
            c
            for c in normal
            if c.best_minutes() <= limit and c.fewest_changes() <= max_changes
        ]
    normal.sort(key=lambda c: (c.best_minutes(), c.fewest_changes(), c.id))
    regional.sort(key=lambda c: (c.best_minutes(), c.fewest_changes(), c.id))
    order = {name: i for i, name in enumerate(pinned)}
    keep = sorted((c for c in normal if c.name in pinned), key=lambda c: order[c.name])
    keep += [c for c in normal if c.name not in pinned][
        : max(0, max_corridors - len(keep))
    ]
    normal = keep
    chosen = normal + regional[:max_regional]
    for i, c in enumerate(x for x in chosen if not x.regional):
        c.color = PALETTE[i % len(PALETTE)]
    for c in chosen:
        if c.regional:
            c.color = REGIONAL_COLOR
    return chosen


def pick_journeys(
    chosen: list[Corridor], direction: str, limit: int
) -> list[tuple[Corridor, Journey]]:
    """Round-robin across corridors (best first) up to ``limit``, sorted by time."""
    queues = [(c, list(c.journeys.get(direction, []))) for c in chosen]
    out: list[tuple[Corridor, Journey]] = []
    while len(out) < limit and any(q for _, q in queues):
        for c, q in queues:
            if q and len(out) < limit:
                out.append((c, q.pop(0)))
    return sorted(out, key=lambda cj: (cj[1].dep, cj[1].arr))


def simplify(
    pts: list[tuple[float, float]], keep: set[int], tol_km: float = 4.0
) -> list[int]:
    """Douglas-Peucker on lat/lon points; indices in ``keep`` always survive."""
    if len(pts) <= 2:
        return list(range(len(pts)))
    lat0 = math.radians(sum(p[0] for p in pts) / len(pts))
    xy = [(p[1] * 111.32 * math.cos(lat0), p[0] * 110.57) for p in pts]
    anchors = sorted({0, len(pts) - 1} | keep)
    out: set[int] = set(anchors)

    def rec(i: int, j: int) -> None:
        (x1, y1), (x2, y2) = xy[i], xy[j]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-9
        worst, wi = 0.0, -1
        for k in range(i + 1, j):
            x, y = xy[k]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            if d > worst:
                worst, wi = d, k
        if worst > tol_km and wi > 0:
            out.add(wi)
            rec(i, wi)
            rec(wi, j)

    for a, b in itertools.pairwise(anchors):
        rec(a, b)
    return sorted(out)
