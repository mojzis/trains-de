"""A tiny synthetic GTFS feed, written as a zip, for router tests.

Line of stations A - B - C - D - E, 55 km apart, running north.

* T1  RE1   A 08:00 -> B 09:00                     (weekdays)
* T2  ICE 9 B 09:04 -> C 10:00                     (weekdays; 4 min at B: missed)
* T3  ICE 9 B 09:10 -> C 10:05                     (WK2: weekdays, not Tue
                                                    2026-10-06, extra Sun 10-04)
* T4  RB3   A 08:30 -> C 11:00                     (weekdays; slow, no change)
* T5  RE1   C 23:30 -> D 24:40 -> E 25:10          (weekdays; past midnight)
* T6  RJ    A 06:00 -> B 07:00                     (weekdays; Czech fragment)
* T7  27    B 07:04 -> E 09:00                     (weekdays; its German part)
* T8  bus   A 07:00 -> E 07:30                     (weekdays; must be ignored)
"""

from __future__ import annotations

import zipfile
from pathlib import Path

STOPS = {
    "A": ("Alpha Hbf", 50.0),
    "B": ("Beta", 50.5),
    "C": ("Gamma Hbf", 51.0),
    "D": ("Delta", 51.5),
    "E": ("Epsilon", 52.0),
}

TRIPS = [
    # Columns: trip id, route id, service id, stops as (stop, arrival, departure).
    ("T1", "R_RE1", "WK", [("A", "08:00", "08:00"), ("B", "09:00", "09:00")]),
    ("T2", "R_ICE", "WK", [("B", "09:04", "09:04"), ("C", "10:00", "10:00")]),
    ("T3", "R_ICE", "WK2", [("B", "09:10", "09:10"), ("C", "10:05", "10:05")]),
    ("T4", "R_RB3", "WK", [("A", "08:30", "08:30"), ("C", "11:00", "11:00")]),
    (
        "T5",
        "R_RE1",
        "WK",
        [("C", "23:30", "23:30"), ("D", "24:40", "24:41"), ("E", "25:10", "25:10")],
    ),
    ("T6", "R_RJ", "WK", [("A", "06:00", "06:00"), ("B", "07:00", "07:00")]),
    ("T7", "R_27", "WK", [("B", "07:04", "07:04"), ("E", "09:00", "09:00")]),
    ("T8", "R_BUS", "WK", [("A", "07:00", "07:00"), ("E", "07:30", "07:30")]),
]


def _csv(rows: list[list[str]]) -> str:
    return "\n".join(",".join(r) for r in rows) + "\n"


def write_fixture(path: Path) -> Path:
    """Write the fixture feed to ``path`` (a .zip) and return it."""
    files = {
        "agency.txt": _csv(
            [
                ["agency_id", "agency_name", "agency_url", "agency_timezone"],
                ["reg", "Test Regio", "https://example.org", "Europe/Berlin"],
                ["dbfv", "DB Fernverkehr AG", "https://example.org", "Europe/Berlin"],
                ["cd", "Ceske Drahy", "https://example.org", "Europe/Berlin"],
            ]
        ),
        "routes.txt": _csv(
            [
                [
                    "route_id",
                    "agency_id",
                    "route_short_name",
                    "route_long_name",
                    "route_type",
                ],
                ["R_RE1", "reg", "RE1", "", "2"],
                ["R_ICE", "dbfv", "ICE 9", "", "2"],
                ["R_RB3", "reg", "RB3", "", "106"],
                ["R_RJ", "cd", "RJ", "", "2"],
                ["R_27", "dbfv", "27", "", "2"],
                ["R_BUS", "reg", "Bus 1", "", "3"],
            ]
        ),
        "stops.txt": _csv(
            [["stop_id", "stop_name", "stop_lat", "stop_lon"]]
            + [[k, n, str(lat), "10.0"] for k, (n, lat) in STOPS.items()]
        ),
        "trips.txt": _csv(
            [["route_id", "service_id", "trip_id"]]
            + [[r, s, t] for t, r, s, _ in TRIPS]
        ),
        "stop_times.txt": _csv(
            [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]]
            + [
                [t, f"{a}:00", f"{d}:00", stop, str(i)]
                for t, _, _, sts in TRIPS
                for i, (stop, a, d) in enumerate(sts)
            ]
        ),
        "calendar.txt": _csv(
            [
                [
                    "service_id",
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                    "start_date",
                    "end_date",
                ],
                ["WK", "1", "1", "1", "1", "1", "0", "0", "20260101", "20261231"],
                ["WK2", "1", "1", "1", "1", "1", "0", "0", "20260101", "20261231"],
            ]
        ),
        "calendar_dates.txt": _csv(
            [
                ["service_id", "date", "exception_type"],
                ["WK2", "20261006", "2"],
                ["WK2", "20261004", "1"],
            ]
        ),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return path
