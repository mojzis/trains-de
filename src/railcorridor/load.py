"""Load GTFS zips into DuckDB, one schema per feed, rail only."""

from __future__ import annotations

import datetime as dt
import re
import tempfile
import zipfile
from pathlib import Path

import duckdb

from railcorridor.config import FEEDS

# route_type values that are rail: 2 plus the extended 100-117 range.
RAIL_TYPES_SQL = "(route_type = 2 OR route_type BETWEEN 100 AND 117)"

_TIME = (
    "CASE WHEN nullif(trim({c}), '') IS NULL THEN NULL ELSE "
    "split_part(trim({c}), ':', 1)::INT * 3600 + "
    "split_part(trim({c}), ':', 2)::INT * 60 + "
    "split_part(trim({c}), ':', 3)::INT END"
)


def _col(cols: set[str], name: str, cast: str = "VARCHAR") -> str:
    if name in cols:
        return f"nullif(trim({name}), '')::{cast} AS {name}"
    return f"NULL::{cast} AS {name}"


def _flag(cols: set[str], name: str) -> str:
    """Optional 0/1/2/3 stop_times flag, 0 when missing or blank."""
    if name not in cols:
        return "0"
    return f"coalesce(nullif(trim(st.{name}), '')::INT, 0)"


def _read(con: duckdb.DuckDBPyConnection, path: Path) -> tuple[str, set[str]]:
    rel = f"read_csv('{path.as_posix()}', header=true, all_varchar=true, quote='\"')"
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()}
    return rel, cols


def _date(c: str) -> str:
    return f"strptime(trim({c}), '%Y%m%d')::DATE"


def ident(name: str) -> str:
    """``name`` if it is a safe SQL identifier (feed schema names), else raise."""
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        msg = f"invalid feed name {name!r}: use lowercase letters, digits, _"
        raise ValueError(msg)
    return name


def load_feed(con: duckdb.DuckDBPyConnection, name: str, zip_path: Path) -> None:
    """(Re)create schema ``name`` from ``zip_path``, keeping rail only."""
    ident(name)
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        _load_dir(con, name, Path(tmp))
    _record_feed(con, name, zip_path)


def _load_dir(con: duckdb.DuckDBPyConnection, schema: str, d: Path) -> None:
    con.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    con.execute(f"CREATE SCHEMA {schema}")
    s = schema

    rel, cols = _read(con, d / "agency.txt")
    con.execute(
        f"CREATE TABLE {s}.agency AS SELECT {_col(cols, 'agency_id')}, "
        f"{_col(cols, 'agency_name')} FROM {rel}"
    )

    rel, cols = _read(con, d / "routes.txt")
    con.execute(
        f"""CREATE TABLE {s}.routes AS SELECT * FROM (
            SELECT {_col(cols, "route_id")}, {_col(cols, "agency_id")},
                   {_col(cols, "route_short_name")}, {_col(cols, "route_long_name")},
                   {_col(cols, "route_type", "INT")}
            FROM {rel}) WHERE {RAIL_TYPES_SQL}"""
    )

    rel, cols = _read(con, d / "trips.txt")
    con.execute(
        f"""CREATE TABLE {s}.trips AS SELECT t.* FROM (
            SELECT {_col(cols, "trip_id")}, {_col(cols, "route_id")},
                   {_col(cols, "service_id")}, {_col(cols, "trip_short_name")},
                   {_col(cols, "block_id")}, {_col(cols, "shape_id")}
            FROM {rel}) t SEMI JOIN {s}.routes r ON r.route_id = t.route_id"""
    )

    rel, cols = _read(con, d / "stop_times.txt")
    arr = _TIME.format(c="arrival_time")
    dep = _TIME.format(c="departure_time")
    con.execute(
        f"""CREATE TABLE {s}.stop_times AS
            SELECT trip_id, stop_id, stop_sequence,
                   coalesce(arr, dep) AS arr, coalesce(dep, arr) AS dep,
                   pickup_type, drop_off_type
            FROM (
              SELECT trim(st.trip_id) AS trip_id, trim(st.stop_id) AS stop_id,
                     st.stop_sequence::INT AS stop_sequence,
                     {arr} AS arr, {dep} AS dep,
                     {_flag(cols, "pickup_type")} AS pickup_type,
                     {_flag(cols, "drop_off_type")} AS drop_off_type
              FROM {rel} st SEMI JOIN {s}.trips t ON t.trip_id = trim(st.trip_id)
            ) WHERE coalesce(arr, dep) IS NOT NULL
            ORDER BY trip_id, stop_sequence"""
    )

    rel, cols = _read(con, d / "stops.txt")
    con.execute(
        f"""CREATE TABLE {s}.stops_all AS SELECT {_col(cols, "stop_id")},
                   {_col(cols, "stop_name")}, {_col(cols, "stop_lat", "DOUBLE")},
                   {_col(cols, "stop_lon", "DOUBLE")}, {_col(cols, "parent_station")},
                   {_col(cols, "location_type", "INT")}
            FROM {rel}"""
    )
    con.execute(
        f"""CREATE TABLE {s}.stops AS
            WITH used AS (SELECT DISTINCT stop_id FROM {s}.stop_times)
            SELECT * FROM {s}.stops_all WHERE stop_id IN (SELECT stop_id FROM used)
               OR stop_id IN (SELECT parent_station FROM {s}.stops_all
                              WHERE stop_id IN (SELECT stop_id FROM used))"""
    )
    con.execute(f"DROP TABLE {s}.stops_all")

    if (d / "calendar.txt").exists():
        rel, cols = _read(con, d / "calendar.txt")
        days = ", ".join(
            f"coalesce(nullif(trim({c}), '')::INT, 0) AS {c}"
            for c in (
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            )
        )
        con.execute(
            f"""CREATE TABLE {s}.calendar AS SELECT trim(service_id) AS service_id,
                   {days}, {_date("start_date")} AS start_date,
                   {_date("end_date")} AS end_date FROM {rel}"""
        )
    else:
        con.execute(
            f"""CREATE TABLE {s}.calendar (service_id VARCHAR, monday INT,
                tuesday INT, wednesday INT, thursday INT, friday INT, saturday INT,
                sunday INT, start_date DATE, end_date DATE)"""
        )

    if (d / "calendar_dates.txt").exists():
        rel, _ = _read(con, d / "calendar_dates.txt")
        con.execute(
            f"""CREATE TABLE {s}.calendar_dates AS
                SELECT trim(c.service_id) AS service_id, {_date("c.date")} AS date,
                       trim(c.exception_type)::INT AS exception_type
                FROM {rel} c SEMI JOIN {s}.trips t
                  ON t.service_id = trim(c.service_id)"""
        )
    else:
        con.execute(
            f"CREATE TABLE {s}.calendar_dates (service_id VARCHAR, date DATE, "
            "exception_type INT)"
        )

    con.execute(
        f"""CREATE TABLE {s}.transfers (from_stop_id VARCHAR, to_stop_id VARCHAR,
            transfer_type INT, min_transfer_time INT, from_trip_id VARCHAR,
            to_trip_id VARCHAR, from_route_id VARCHAR, to_route_id VARCHAR)"""
    )
    if (d / "transfers.txt").exists():
        rel, cols = _read(con, d / "transfers.txt")
        con.execute(
            f"""INSERT INTO {s}.transfers SELECT {_col(cols, "from_stop_id")},
                {_col(cols, "to_stop_id")}, {_col(cols, "transfer_type", "INT")},
                {_col(cols, "min_transfer_time", "INT")},
                {_col(cols, "from_trip_id")}, {_col(cols, "to_trip_id")},
                {_col(cols, "from_route_id")}, {_col(cols, "to_route_id")}
                FROM {rel}"""
        )

    con.execute(f"CREATE TABLE {s}.feed_info (key VARCHAR, value VARCHAR)")
    if (d / "feed_info.txt").exists():
        rel, cols = _read(con, d / "feed_info.txt")
        for c in sorted(cols):
            con.execute(
                f"INSERT INTO {s}.feed_info SELECT '{c}', any_value({c}) FROM {rel}"
            )


def _record_feed(con: duckdb.DuckDBPyConnection, name: str, zip_path: Path) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS main.feeds (name VARCHAR PRIMARY KEY,
           file VARCHAR, loaded_at TIMESTAMP, valid_from DATE, valid_to DATE)"""
    )
    lo, hi = con.execute(
        f"""SELECT min(d), max(d) FROM (
              SELECT start_date AS d FROM {name}.calendar
              UNION ALL SELECT end_date FROM {name}.calendar
              UNION ALL SELECT date FROM {name}.calendar_dates
                        WHERE exception_type = 1)"""
    ).fetchone() or (None, None)
    con.execute("DELETE FROM main.feeds WHERE name = ?", [name])
    con.execute(
        "INSERT INTO main.feeds VALUES (?, ?, ?, ?, ?)",
        [name, zip_path.name, dt.datetime.now(dt.UTC).replace(tzinfo=None), lo, hi],
    )


def loaded_feeds(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Names of feeds present in the database, in registry order."""
    try:
        names = {r[0] for r in con.execute("SELECT name FROM main.feeds").fetchall()}
    except duckdb.CatalogException:
        return []
    ordered = [n for n in FEEDS if n in names]
    return ordered + sorted(names - set(ordered))
