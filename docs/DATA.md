# Data research: feeds for Praha ⇄ Lüneburg

Checked on 2026-09-28 by downloading each feed and querying it in DuckDB.

## Summary

The German gtfs.de feeds alone cover every leg the explorer needs for the
long-distance corridors, including the Czech sections of the cross-border
trains. They cut those trains into fragments, which the loader joins again.
They do **not** contain Czech domestic trains (Praha–Ústí–Děčín locals,
Praha–Plzeň–Cheb expresses). The Czech CZPTT GTFS fills that gap and is
optional (`--feed cz_czptt`).

| feed (id) | URL | format | licence | cadence / validity | Czech leg of cross-border trains? |
| --- | --- | --- | --- | --- | --- |
| gtfs.de long distance (`de_fv`) | https://download.gtfs.de/germany/fv_free/latest.zip | GTFS, 0.4 MB | CC BY 4.0 (gtfs.de, data by DELFI e.V.) | rolling window of about 30 days ("cover the next 30 days" per gtfs.de); this copy 2026-09-26 → 2026-10-26, last modified 2026-09-26 | **Yes, partly**: ComfortJet Praha → Děčín as its own trip (agency "Ceske Drahy", route "RJ"), night trains via Praha |
| gtfs.de regional (`de_rv`) | https://download.gtfs.de/germany/rv_free/latest.zip | GTFS, 11 MB | CC BY 4.0 | same as fv | **Yes, partly**: the German part of the ComfortJet as DB line "27" (Hamburg–Berlin–Dresden–Děčín); RE25 Praha–Plzeň–Furth im Wald–Schwandorf–München; U28 into Děčín; ZVON lines Praha–Mladá Boleslav–Liberec; RE33/RB95 Cheb–Marktredwitz–Nürnberg |
| gtfs.de full (`free`) | https://download.gtfs.de/germany/free/latest.zip | GTFS, 297 MB | CC BY 4.0 | same as fv | same rail content as fv + rv, plus buses/trams (not used) |
| DELFI (opendata-oepnv.de) | https://www.opendata-oepnv.de | GTFS / NeTEx | needs a login; licence and cadence not checked |  not checked | same source as gtfs.de, which republishes it without registration (not used) |
| Czech rail CZPTT (`cz_czptt`) | https://motis.obehy.cz/get-feeds/cz-czptt-gtfs.zip | GTFS, 17 MB | CC0 1.0 (as listed by Transitous) | cadence not documented (the copy downloaded on 2026-09-28 was last modified 2026-09-25); covers the whole timetable year 2025-12-14 → 2026-12-12 | **Yes**: all Czech trains, with train numbers (`rj 384`), block ids and transfers, up to the border stop (e.g. to Bad Schandau) |

The Transitous feed list
(`github.com/public-transport/transitous/feeds/cz.json`) was the index for
the Czech feed: CZPTT is converted by JrUtil from Správa železnic's CZPTT
timetable XML (the CIS JŘ open data). The other Czech feeds listed there
(PID, IDS-JMK and the regional ones) are urban or bus networks and aren't needed.

## Findings that shape the code

* **Cross-border trains are split.** The 06:31 Praha ComfortJet is
  `de_fv` trip "RJ" Praha → Děčín (arr 07:58) plus `de_rv` trip "27" Děčín
  08:02 → Hamburg → Flensburg. Without joining, the router counts a change
  at Děčín with a 4-minute connection and discards it. `timetable.join_through_trips`
  glues fragments when they meet at the same station within 10 minutes and
  look like one train: same label or route, or long-distance trains of two
  operators meeting within 30 km of a border point `(Gr)`. Copies of one train in two
  feeds (CZPTT + gtfs.de) are joined mid-trip when they call at an earlier
  station within 5 minutes of each other.
* **No train numbers in gtfs.de.** `trip_short_name` is missing, so labels
  are line names ("ICE 25", "RE25", "RJ"). DB lines published as bare numbers
  with a long-distance agency become "EC 27" / "EN 27N". CZPTT has real
  numbers.
* **No shapes.txt** in any of the feeds, so the map draws simplified
  polylines through the stops.
* **Only CZPTT has transfers.txt**, and only platform/bus pairs. Default
  change times apply: 8 min at hubs, 5 min elsewhere.
* **Border points** (`Schöna(Gr)`, `Furth im Wald(Gr)`) carry
  `pickup_type = drop_off_type = 1`. The router never boards, alights or
  changes there.
* **Service days vary.** In this copy the Praha 08:31 and 10:31 ComfortJets
  have no service 1–6 Oct 2026. They run again from 7 Oct (08:31) and
  11 Oct (10:31). The 06:31 runs every day, Mondays included. The 08:31 runs
  Tuesday to Sunday only, so on Mondays it drops out on its own.

## Praha → Cheb vs Praha → Schwandorf

Praha–Cheb (Czech domestic IC/R) is not in the German feeds, only in
CZPTT. The Cheb–Nürnberg regional trains are in `de_rv`. The western
corridor therefore uses Praha → Plzeň → Furth im Wald → Schwandorf (RE25,
entirely in `de_rv`) → Nürnberg, then the direct ICE 25 Nürnberg →
Lüneburg. That is also the faster way to Nürnberg.
