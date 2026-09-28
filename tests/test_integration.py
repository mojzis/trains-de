"""Smoke test against the real feeds. Run ``uv run railcorridor fetch`` and
``uv run railcorridor build-db`` first, then ``uv run pytest -m integration``.
Skipped when the database is missing (e.g. offline CI)."""

import datetime as dt

import duckdb
import pytest

from railcorridor.config import DB_PATH
from railcorridor.explore import explore, load_pair, parse_window

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def outbound():
    if not DB_PATH.exists():
        pytest.skip(f"{DB_PATH} missing: run fetch + build-db")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    lo, hi = con.execute(
        "SELECT max(valid_from), min(valid_to) FROM main.feeds WHERE name LIKE 'de_%'"
    ).fetchone() or (None, None)
    if lo is None or hi is None:
        pytest.skip("no German feed loaded")
    day = max(lo, dt.datetime.now(dt.UTC).date())
    while day.isoweekday() != 2:  # next Tuesday inside the feeds' window
        day += dt.timedelta(days=1)
    if day > hi:
        pytest.skip("no Tuesday inside the feed validity window")
    pair = load_pair("Praha hl.n.", "Lüneburg")
    res = explore(
        con,
        pair,
        day,
        parse_window("04:00-11:00"),
        both_directions=False,
        log=lambda _: None,
    )
    names = res.tt.names
    rows = []
    for _, j in res.chosen["outbound"]:
        rows.append(
            [
                (
                    leg.trip.label,
                    names[leg.from_station],
                    names[leg.to_station],
                    leg.dep,
                )
                for leg in j.legs
            ]
        )
    return rows


def test_comfortjet_0631_with_one_change_in_hamburg(outbound):
    hits = [
        j
        for j in outbound
        if j[0][3] == 6 * 3600 + 31 * 60 and len(j) == 2 and j[0][2] == "Hamburg Hbf"
    ]
    assert hits, f"no 06:31 Praha -> Hamburg Hbf -> Lüneburg journey in {outbound}"
    assert hits[0][0][0] in {"RJ", "EC 27"}


def test_direct_ice_nurnberg_to_luneburg(outbound):
    hits = [
        j
        for j in outbound
        if any(leg[1] == "Nürnberg Hbf" and leg[2] == "Lüneburg" for leg in j)
    ]
    assert hits, f"no journey with a direct Nürnberg Hbf -> Lüneburg leg in {outbound}"
    assert any(
        leg[0].startswith("ICE") for j in hits for leg in j if leg[1] == "Nürnberg Hbf"
    )
