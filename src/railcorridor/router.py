"""Journey search: RAPTOR over one day's station-level timetable.

Round k holds the earliest arrival using at most k trips, so the rounds give
the Pareto set on (arrival, changes) for one departure time. ``profile`` runs
that for every departure in a window.

An optional *via* station adds a second label layer: a ride that passes (or
changes at) the via station moves to layer 1, and only layer-1 arrivals at the
target count. That finds the best journeys through a given corridor even when
another corridor dominates them overall.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

from railcorridor.timetable import Timetable, Trip

INF = 1 << 40


@dataclass(frozen=True)
class Leg:
    """Ride trip ``trip`` from stop index ``board`` to stop index ``alight``."""

    trip: Trip
    board: int
    alight: int

    @property
    def from_station(self) -> int:
        """Station index where the leg starts."""
        return self.trip.stations[self.board]

    @property
    def to_station(self) -> int:
        """Station index where the leg ends."""
        return self.trip.stations[self.alight]

    @property
    def dep(self) -> int:
        """Departure time (s after midnight)."""
        return self.trip.dep[self.board]

    @property
    def arr(self) -> int:
        """Arrival time (s after midnight)."""
        return self.trip.arr[self.alight]


@dataclass(frozen=True)
class Journey:
    """A sequence of legs."""

    legs: tuple[Leg, ...]

    @property
    def dep(self) -> int:
        """Departure from the origin."""
        return self.legs[0].dep

    @property
    def arr(self) -> int:
        """Arrival at the destination."""
        return self.legs[-1].arr

    @property
    def changes(self) -> int:
        """Number of changes."""
        return len(self.legs) - 1

    @property
    def min_slack(self) -> int:
        """Shortest change time in seconds (large when there is no change)."""
        gaps = [b.dep - a.arr for a, b in zip(self.legs, self.legs[1:], strict=False)]
        return min(gaps, default=1 << 30)

    def signature(self) -> tuple:
        """Identity for de-duplication: times and change stations."""
        return (
            self.dep,
            self.arr,
            tuple((lg.from_station, lg.to_station, lg.dep, lg.arr) for lg in self.legs),
        )


@dataclass
class _Pattern:
    stations: list[int]
    trips: list[Trip]
    deps: list[list[int]]  # deps[i][pos] sorted by pos (FIFO)
    board: list[bool]
    alight: list[bool]


class Network:
    """Route patterns built from a timetable, ready for RAPTOR queries."""

    def __init__(self, tt: Timetable) -> None:
        """Group trips into FIFO patterns and index them by station."""
        self.tt = tt
        groups: dict[tuple, list[Trip]] = defaultdict(list)
        for t in tt.trips:
            key = (tuple(t.stations), tuple(t.board), tuple(t.alight))
            groups[key].append(t)
        self.patterns: list[_Pattern] = []
        for (stations, board, alight), trips in groups.items():
            trips.sort(key=lambda t: (t.dep[0], t.arr[-1]))
            subs: list[list[Trip]] = []
            for t in trips:
                for sub in subs:
                    last = sub[-1]
                    if all(
                        d1 <= d2 and a1 <= a2
                        for d1, d2, a1, a2 in zip(
                            last.dep, t.dep, last.arr, t.arr, strict=True
                        )
                    ):
                        sub.append(t)
                        break
                else:
                    subs.append([t])
            for sub in subs:
                self.patterns.append(
                    _Pattern(
                        stations=list(stations),
                        trips=sub,
                        deps=[[t.dep[i] for t in sub] for i in range(len(stations))],
                        board=list(board),
                        alight=list(alight),
                    )
                )
        self.at_station: list[list[tuple[int, int]]] = [[] for _ in tt.station_ids]
        for pi, p in enumerate(self.patterns):
            for i, s in enumerate(p.stations):
                self.at_station[s].append((pi, i))

    def departures(self, origin: int, t_from: int, t_to: int) -> list[int]:
        """Distinct departure times from ``origin`` within [t_from, t_to]."""
        out: set[int] = set()
        for pi, i in self.at_station[origin]:
            p = self.patterns[pi]
            if not p.board[i] or i == len(p.stations) - 1:
                continue
            out.update(d for d in p.deps[i] if t_from <= d <= t_to)
        return sorted(out)

    def query(
        self,
        origin: int,
        t0: int,
        target: int,
        *,
        max_rounds: int = 7,
        via: int | None = None,
    ) -> list[Journey]:
        """Pareto journeys (arrival, changes) leaving ``origin`` at or after t0."""
        tt = self.tt
        n = len(tt.station_ids)
        layers = 1 if via is None or via in (origin, target) else 2
        top = layers - 1
        start_layer = 0
        tau = [[[INF] * n for _ in range(layers)]]
        best = [[INF] * n for _ in range(layers)]
        parent: list[list[dict[int, tuple[int, int, int, int, int]]]] = [
            [{} for _ in range(layers)]
        ]
        tau[0][start_layer][origin] = t0
        best[start_layer][origin] = t0
        marked = {origin}
        change = tt.change_s
        no_tr = tt.no_transfer
        for k in range(1, max_rounds + 1):
            prev = tau[k - 1]
            cur_tau = [list(row) for row in prev]
            tau.append(cur_tau)
            par: list[dict[int, tuple[int, int, int, int, int]]] = [
                {} for _ in range(layers)
            ]
            parent.append(par)
            q: dict[int, int] = {}
            for s in marked:
                for pi, i in self.at_station[s]:
                    if q.get(pi, INF) > i:
                        q[pi] = i
            marked = set()
            for pi, i0 in q.items():
                p = self.patterns[pi]
                stations = p.stations
                trips = p.trips
                # per layer: (pos, board index, board layer)
                ride: list[tuple[int, int, int] | None] = [None] * layers
                for i in range(i0, len(stations)):
                    s = stations[i]
                    if p.alight[i] and s not in no_tr:
                        for lay in range(layers):
                            r = ride[lay]
                            if r is None:
                                continue
                            a = trips[r[0]].arr[i]
                            out = 1 if (layers == 2 and s == via) else lay
                            if a < best[out][s] and a < best[top][target]:
                                cur_tau[out][s] = a
                                best[out][s] = a
                                par[out][s] = (pi, r[0], r[1], i, r[2])
                                marked.add(s)
                    if layers == 2 and s == via and ride[0] is not None:
                        r0 = ride[0]
                        if ride[1] is None or r0[0] < ride[1][0]:
                            ride[1] = r0
                    if not p.board[i] or s in no_tr or i == len(stations) - 1:
                        continue
                    for lay in range(layers):
                        t_lab = prev[lay][s]
                        if t_lab >= INF:
                            continue
                        ready = (
                            t_lab
                            if (s == origin and t_lab == t0)
                            else (t_lab + change[s])
                        )
                        pos = bisect_left(p.deps[i], ready)
                        r = ride[lay]
                        if pos < len(trips) and (r is None or pos < r[0]):
                            ride[lay] = (pos, i, lay)
            if not marked:
                break
        journeys = []
        seen = set()
        for k in range(1, len(parent)):
            if target in parent[k][top]:
                j = self._rebuild(parent, k, top, target, origin)
                if j and j.signature() not in seen:
                    seen.add(j.signature())
                    journeys.append(j)
        return journeys

    def _rebuild(
        self,
        parent: list[list[dict[int, tuple[int, int, int, int, int]]]],
        k: int,
        layer: int,
        s: int,
        origin: int,
    ) -> Journey | None:
        legs: list[Leg] = []
        while k > 0:
            entry = parent[k][layer].get(s)
            if entry is None:
                k -= 1
                continue
            pi, pos, bi, ai, blayer = entry
            p = self.patterns[pi]
            legs.append(Leg(p.trips[pos], bi, ai))
            s, layer, k = p.stations[bi], blayer, k - 1
        if s != origin or not legs:
            return None
        return Journey(tuple(reversed(legs)))

    def polish(self, j: Journey) -> Journey:
        """Move each change to the best station both trains serve.

        RAPTOR changes at the first station where the connection works
        (e.g. Hamburg Dammtor); a hub with more slack (Hamburg Hbf) reaches the
        same trains and is what a traveller would pick. Times stay the same.
        """
        tt = self.tt
        legs = list(j.legs)
        for k in range(len(legs) - 1):
            a, b = legs[k], legs[k + 1]
            b_pos = {s: i for i, s in enumerate(b.trip.stations[: b.alight])}
            best: tuple | None = None
            for pa in range(a.board + 1, len(a.trip.stations)):
                s = a.trip.stations[pa]
                pb = b_pos.get(s)
                if pb is None or not a.trip.alight[pa] or not b.trip.board[pb]:
                    continue
                if s in tt.no_transfer:
                    continue
                slack = b.trip.dep[pb] - a.trip.arr[pa] - tt.change_s[s]
                if slack < 0:
                    continue
                score = (tt.hub[s], slack, pa, pb)
                if best is None or score > best[0]:
                    best = (score, pa, pb)
            if best is not None:
                _, pa, pb = best
                legs[k] = Leg(a.trip, a.board, pa)
                legs[k + 1] = Leg(b.trip, pb, b.alight)
        return Journey(tuple(legs))

    def profile(
        self,
        origin: int,
        target: int,
        t_from: int,
        t_to: int,
        *,
        max_rounds: int = 7,
        via: int | None = None,
    ) -> list[Journey]:
        """Journeys for every departure in the window, de-duplicated."""
        out: dict[tuple, Journey] = {}
        for t in self.departures(origin, t_from, t_to):
            for raw in self.query(origin, t, target, max_rounds=max_rounds, via=via):
                j = self.polish(raw)
                if t_from <= j.dep <= t_to:
                    out.setdefault(j.signature(), j)
        return sorted(out.values(), key=lambda j: (j.dep, j.arr, j.changes))


def pareto(journeys: list[Journey]) -> list[Journey]:
    """Drop journeys dominated on (later departure, earlier arrival, fewer changes).

    Of journeys with identical times and changes only the one with the most
    generous change time is kept.
    """
    keep: list[Journey] = []
    seen: set[tuple[int, int, int]] = set()
    # among equal (dep, arr, changes) the safest connection comes first
    for j in sorted(journeys, key=lambda j: (-j.dep, j.arr, j.changes, -j.min_slack)):
        key = (j.dep, j.arr, j.changes)
        if key in seen:
            continue
        dominated = any(
            o.dep >= j.dep and o.arr <= j.arr and o.changes <= j.changes for o in keep
        )
        if not dominated:
            keep.append(j)
            seen.add(key)
    return sorted(keep, key=lambda j: (j.dep, j.arr))
