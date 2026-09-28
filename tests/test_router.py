import datetime as dt

from railcorridor.config import RoutingConfig
from railcorridor.router import pareto
from tests.conftest import summary

MON = dt.date(2026, 10, 5)
TUE_NO_T3 = dt.date(2026, 10, 6)  # WK2 removed by calendar_dates
SUN_EXTRA = dt.date(2026, 10, 4)  # WK2 added by calendar_dates, WK off


def test_transfer_beats_slow_direct_train(route):
    js = route("Alpha Hbf", "Gamma Hbf", MON, "08:00")
    # 0 changes: RB3 08:30 -> 11:00; 1 change: RE1 + ICE at Beta -> 10:05
    assert summary(js) == [("08:30", "11:00", 0), ("08:00", "10:05", 1)]


def test_connection_with_too_little_change_time_is_missed(route):
    js = route("Alpha Hbf", "Gamma Hbf", MON, "08:00")
    changed = next(j for j in js if j.changes == 1)
    # 09:04 ICE leaves 4 min after arrival; default change time is 5 min
    assert changed.legs[1].dep == 9 * 3600 + 10 * 60


def test_change_time_floor_drops_short_connection(route):
    # the RE1 -> ICE change at Beta has 10 min; a 15 min floor rules it out
    js = route("Alpha Hbf", "Gamma Hbf", MON, "08:00", RoutingConfig(min_change_min=15))
    assert all(j.changes == 0 for j in js)


def test_calendar_exception_removes_a_service_day(route):
    js = route("Alpha Hbf", "Gamma Hbf", TUE_NO_T3, "08:00")
    assert summary(js) == [("08:30", "11:00", 0)]


def test_calendar_exception_adds_a_service_day(route):
    js = route("Beta", "Gamma Hbf", SUN_EXTRA, "09:00")
    assert summary(js) == [("09:10", "10:05", 0)]


def test_trip_past_midnight_arrives_after_24h(route):
    js = route("Gamma Hbf", "Epsilon", MON, "23:00")
    assert summary(js) == [("23:30", "25:10", 0)]


def test_previous_days_trip_is_boardable_after_midnight(route):
    # T5 of Monday reaches Delta at 24:41, i.e. Tuesday 00:41
    js = route("Delta", "Epsilon", TUE_NO_T3, "00:00")
    assert summary(js) == [("00:41", "01:10", 0)]


def test_train_split_across_trips_is_one_leg(route):
    js = route("Alpha Hbf", "Epsilon", MON, "05:30")
    first = js[0]
    assert (first.changes, first.legs[0].trip.label) == (0, "RJ")
    assert summary(js)[0] == ("06:00", "09:00", 0)


def test_bus_routes_are_not_loaded(route):
    js = route("Alpha Hbf", "Epsilon", MON, "06:30")
    # the 07:00 bus would arrive 07:30; by rail it is RB3 + the overnight RE1
    assert summary(js) == [("08:30", "25:10", 1)]


def test_pareto_drops_dominated_and_keeps_tradeoffs(route):
    js = route("Alpha Hbf", "Gamma Hbf", MON, "08:00")
    js += route("Alpha Hbf", "Gamma Hbf", MON, "08:20")
    assert summary(pareto(js)) == [("08:00", "10:05", 1), ("08:30", "11:00", 0)]
