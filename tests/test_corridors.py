"""Golden-file test for corridor grouping and naming.

Regenerate the golden file after an intended change with
``UPDATE_GOLDEN=1 uv run pytest tests/test_corridors.py``.
"""

import json
import os
from pathlib import Path

from railcorridor.corridors import Places, group, select
from railcorridor.router import Journey, Leg
from railcorridor.timetable import Trip

GOLDEN = Path(__file__).parent / "golden" / "corridors.json"
PLACES = Places(
    [
        "Berlin",
        "Hannover",
        "Nürnberg",
        "Leipzig",
        "Magdeburg",
        "Hamburg",
        "Schwandorf",
        "Uelzen",
        "Stendal",
        "Dresden",
        "Plzeň",
        "Děčín",
    ]
)
EXCLUDE = {"Praha", "Lüneburg"}

# (label, long_distance, [(station, "HH:MM"), ...]) per leg
JOURNEYS = {
    "outbound": {
        "comfortjet": [
            (
                "RJ",
                True,
                [
                    ("Praha hl.n.", "06:31"),
                    ("Děčín hl.n.", "07:58"),
                    ("Dresden Hbf", "08:53"),
                    ("Berlin Hbf", "10:29"),
                    ("Hamburg Hbf", "12:24"),
                ],
            ),
            ("ICE 25", True, [("Hamburg Hbf", "13:01"), ("Lüneburg", "13:28")]),
        ],
        "via-leipzig-hannover": [
            ("RJ", True, [("Praha hl.n.", "06:31"), ("Dresden Hbf", "08:50")]),
            (
                "IC 55",
                True,
                [
                    ("Dresden Hbf", "09:14"),
                    ("Leipzig Hbf", "10:25"),
                    ("Magdeburg Hbf", "11:40"),
                    ("Hannover Hbf", "13:05"),
                ],
            ),
            (
                "ICE 25",
                True,
                [("Hannover Hbf", "13:31"), ("Uelzen", "14:05"), ("Lüneburg", "14:24")],
            ),
        ],
        "west": [
            (
                "RE25",
                False,
                [
                    ("Praha hl.n.", "05:35"),
                    ("Plzen hl.n.", "07:10"),
                    ("Schwandorf", "09:05"),
                ],
            ),
            ("RE40", False, [("Schwandorf", "09:10"), ("Nürnberg Hbf", "10:15")]),
            (
                "ICE 25",
                True,
                [
                    ("Nürnberg Hbf", "10:31"),
                    ("Hannover Hbf", "13:30"),
                    ("Lüneburg", "14:25"),
                ],
            ),
        ],
        "regional": [
            ("U28", False, [("Děčín hl.n.", "07:40"), ("Bad Schandau", "08:05")]),
            ("S1", False, [("Bad Schandau", "08:14"), ("Dresden Hbf", "09:00")]),
            ("RE50", False, [("Dresden Hbf", "09:10"), ("Leipzig Hbf", "10:50")]),
            ("RE13", False, [("Leipzig Hbf", "11:05"), ("Magdeburg Hbf", "12:30")]),
            (
                "RE20",
                False,
                [("Magdeburg Hbf", "12:45"), ("Stendal", "13:40"), ("Uelzen", "14:55")],
            ),
            ("RE3", False, [("Uelzen", "15:10"), ("Lüneburg", "15:30")]),
        ],
        "no-known-place": [
            ("RB1", False, [("Praha hl.n.", "05:00"), ("Kolín", "06:00")]),
            ("RB2", False, [("Kolín", "06:10"), ("Lüneburg", "20:00")]),
        ],
    },
    "return": {
        "comfortjet-back": [
            (
                "RB31",
                False,
                [
                    ("Lüneburg", "04:34"),
                    ("Hamburg-Harburg", "05:05"),
                    ("Hamburg Hbf", "05:20"),
                ],
            ),
            (
                "RJ",
                True,
                [
                    ("Hamburg Hbf", "05:34"),
                    ("Berlin Hbf", "07:28"),
                    ("Dresden Hbf", "09:10"),
                    ("Praha hl.n.", "11:25"),
                ],
            ),
        ],
    },
}


def _hm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 3600 + int(m) * 60


def _build() -> tuple[dict[str, list[Journey]], list[str], dict[int, str]]:
    names: list[str] = []
    index: dict[str, int] = {}
    ids: dict[int, str] = {}
    out: dict[str, list[Journey]] = {}
    for direction, js in JOURNEYS.items():
        out[direction] = []
        for key, legs in js.items():
            built = []
            for n, (label, ld, stops) in enumerate(legs):
                st = []
                for name, _ in stops:
                    if name not in index:
                        index[name] = len(names)
                        names.append(name)
                    st.append(index[name])
                times = [_hm(t) for _, t in stops]
                trip = Trip(
                    key=f"{key}:{n}",
                    feed="t",
                    route_id=label,
                    agency="",
                    label=label,
                    category=label.split()[0],
                    long_distance=ld,
                    label_quality=2,
                    route_type=2,
                    stations=st,
                    arr=times,
                    dep=times,
                    board=[True] * len(st),
                    alight=[True] * len(st),
                )
                built.append(Leg(trip, 0, len(st) - 1))
            j = Journey(tuple(built))
            ids[id(j)] = key
            out[direction].append(j)
    return out, names, ids


def _golden(got: dict) -> dict:
    """The golden data; rewritten from ``got`` when UPDATE_GOLDEN is set."""
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n")
    return json.loads(GOLDEN.read_text())


def test_corridor_grouping_matches_golden_file():
    journeys, names, ids = _build()
    corridors = group(journeys, names, PLACES, EXCLUDE)
    chosen = select(corridors, ratio=10, slack_min=24 * 60, max_changes=9)
    got = {
        c.id: {
            "name": c.name,
            "regional": c.regional,
            "color": c.color[0],
            "journeys": {
                d: sorted(ids[id(j)] for j in js) for d, js in c.journeys.items()
            },
        }
        for c in chosen
    }
    assert got == _golden(got)
