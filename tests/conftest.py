from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

from railcorridor.config import RoutingConfig
from railcorridor.load import load_feed
from railcorridor.router import Journey, Network
from railcorridor.stations import build_stations
from railcorridor.timetable import load_timetable
from tests.gtfs_fixture import write_fixture


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> duckdb.DuckDBPyConnection:
    tmp = tmp_path_factory.mktemp("gtfs")
    zp = write_fixture(Path(tmp) / "fx_20261001.zip")
    con = duckdb.connect()
    load_feed(con, "fx", zp)
    build_stations(con, ["fx"])
    return con


def _hm(secs: int) -> str:
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}"


@pytest.fixture
def route(
    fixture_db: duckdb.DuckDBPyConnection,
) -> Callable[..., list[Journey]]:
    """route("Alpha Hbf", "Gamma Hbf", date, "08:00") -> Pareto journeys."""

    def run(
        a: str, b: str, date: dt.date, t0: str, cfg: RoutingConfig | None = None
    ) -> list[Journey]:
        tt = load_timetable(fixture_db, date, ["fx"], cfg=cfg)
        net = Network(tt)
        ids = {n: i for i, n in enumerate(tt.names)}
        h, m = t0.split(":")
        return net.query(ids[a], int(h) * 3600 + int(m) * 60, ids[b])

    return run


def summary(js: list[Journey]) -> list[tuple[str, str, int]]:
    return [(_hm(j.dep), _hm(j.arr), j.changes) for j in js]
