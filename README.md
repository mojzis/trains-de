# Rail corridor explorer

Computes real train connections between two cities from GTFS timetables. The
result is a self-contained web page with:

* a schematic map with the corridors painted on it,
* a Gantt-style timeline of departures,
* a leg-by-leg detail panel,
* corridor comparison cards.

The first target is **Praha ⇄ Lüneburg**, morning departures in both
directions. Nothing is hand-entered except fare notes: every time on the page
comes from the feeds. Where the feeds lack a leg, the page shows it as
"not in data".

## Quick start

```sh
uv sync
uv run railcorridor fetch            # data/raw/<feed>_<YYYYMMDD>.zip
uv run railcorridor build-db         # data/gtfs.duckdb (≈45 s)
uv run railcorridor query --from "Praha hl.n." --to "Lüneburg" \
    --date 2026-10-06 --depart-window 04:00-11:00 --both-directions
                                     # out/praha-luneburg/2026-10-06.json (≈35 s on 4 cores)
uv run railcorridor site             # out/praha-luneburg/index.html
open out/praha-luneburg/index.html   # or xdg-open; no server needed
```

Run `query` for several dates (say a Monday and a Tuesday) before `site`. The
page gets a date picker, which makes day-specific differences such as the
Monday trackwork visible side by side.

Options:

* `fetch -f cz_czptt` / `build-db -f de_fv -f de_rv -f cz_czptt` adds the
  Czech feed (see below).
* `query --no-regional` skips the regional-only (Deutschlandticket) search.
* `query --max-journeys N` caps the journeys per direction (default 12).
* `query --one-direction` searches outbound only.
* `query --min-change 15` puts a 15-minute floor on every change and writes
  `<date>@c15.json`. The page then offers a "Changes ≥ 15 min" toggle next to
  the timetabled search. Changes under 10 minutes (`TIGHT_CHANGE_MIN`) are
  drawn orange on the timeline, map and detail panel either way.

Query results in `out/<pair>/*.json` are committed, so dates that have left
the feeds' window stay viewable. The Pages workflow adds fresh results for the
next Monday and Tuesday and renders every JSON file it finds.

The gtfs.de feeds cover only about the next 30 days. A date outside the
window produces a warning on the page, and the missing trains show as
"not in data".

## Feeds and licences

| id | source | licence | used by default |
| --- | --- | --- | --- |
| `de_fv` | [gtfs.de](https://gtfs.de) Germany long-distance rail, data by DELFI e.V. | CC BY 4.0 | yes |
| `de_rv` | [gtfs.de](https://gtfs.de) Germany regional rail, data by DELFI e.V. | CC BY 4.0 | yes |
| `cz_czptt` | Czech rail (Správa železnic CZPTT open data), GTFS by the [Oběhy project](https://obehy.cz) via JrUtil | CC0 1.0 | no |

The page footer repeats this attribution. `docs/DATA.md` has the research
behind the choice. In short:

* The German feeds carry the Czech legs of the cross-border ComfortJets, cut
  into fragments at Děčín, and the Praha–Plzeň–Furth im Wald–Schwandorf trains.
* They lack Czech domestic trains such as Praha–Děčín locals and
  Praha–Plzeň–Cheb.
* Adding `cz_czptt` fills those gaps and brings real train numbers.

## How it works

```
feeds.py     download + cache zips (date-stamped)
load.py      GTFS -> DuckDB, one schema per feed, rail only (route_type 2, 100-117)
stations.py  merge stops across feeds into main.stations
timetable.py one service day at station level; join split trains
labels.py    ICE / IC / EC / RJ / RE / RB / S labels from route/trip/agency
router.py    RAPTOR: Pareto (arrival, changes) per departure, optional via
corridors.py group journeys into named corridors; stats; map geometry
explore.py   run the searches for a pair/date; pairs/<pair>.toml
fares.py     FareProvider interface; TomlFareProvider reads fares.toml
export.py    JSON document (out/<pair>/<date>.json)
site.py      Jinja2 template + static/app.js inlined -> index.html
```

### Stations

Stops collapse onto their `parent_station`. Candidates from all feeds are then
merged when they are closer than 300 m and have compatible normalised names:

* `hl.n.` becomes `hlavní nádraží`, `Hbf` becomes `Hauptbahnhof`, diacritics
  are folded.
* Names match if they share a first word, or one name's words are contained in
  the other's.

Praha hl.n., Dresden Hbf, Berlin Hbf, Hamburg Hbf, Nürnberg Hbf and Hannover
Hbf each resolve to one station. `main.stop_station` records which feed each
stop id came from.

### Service days

`calendar` weekday flags and date range are combined with `calendar_dates`
additions and removals. Trips of the previous service day that run past 24:00
are included, shifted by 24 h. A Monday query therefore shows exactly the
trains the feed says run that Monday.

### Split trains

gtfs.de publishes the 06:31 Praha ComfortJet as two trips:

* "RJ" Praha → Děčín, arriving 07:58, in `de_fv`;
* line "27" Děčín 08:02 → Hamburg, in `de_rv`.

`join_through_trips` glues such fragments back into one trip, so riding
through is not a change. They must meet at the same station within 10 minutes,
must not turn back, and must look like one train:

* the same label or route, or
* long-distance trains of different operators meeting within 30 km of a border
  point, or
* a copy of the same train in another feed.

The detail panel says where a train was joined.

### Routing

RAPTOR over the day's trips, restricted to an ellipse around the two cities:

* Round *k* is the earliest arrival with *k* trains, which gives the Pareto set
  on (arrival, changes) for each departure in the window.
* Change times come from `transfers.txt` where present. Otherwise the default
  is 8 min at hubs and 5 min elsewhere (`RoutingConfig`).
* `min_change_min` (CLI `--min-change`) raises every change time, including
  `transfers.txt` ones, to at least that many minutes.
* Border points `(Gr)` are never used for changing.
* A post-pass moves each change to the best station both trains serve: a hub
  with more slack beats the first possible one, e.g. Hamburg Hbf over
  Hamburg Dammtor.

The fastest corridor would hide the others, so `explore` runs extra searches:

* one per `search_via` station in the pair file, each of which must pass
  through that station;
* one on regional trains only, the Deutschlandticket corridor, which excludes
  ICE/IC/EC/RJ/EN/NJ/FLX and the Czech Ex/SC/LE classes.

### Corridors

A journey becomes the list of *places* it passes, from the pair's `places`
list, marking where it changes. The corridor is named `A → T`:

* T is the last change place.
* A is the most distinctive place among the previous change point and the
  places passed since it.

Return journeys are named in outbound order, so both directions share a
corridor. Examples: `Berlin → Hamburg`, `Leipzig → Hannover`,
`Schwandorf → Nürnberg`, `Magdeburg → Uelzen (regional)`.
`tests/golden/corridors.json` pins this behaviour.

Within a corridor:

* dominated journeys are dropped;
* variants of the same main train keep the one with the fewest changes;
* journeys much slower than the corridor's best are trimmed.

Corridors much slower than the best overall are dropped. Those listed under
`expected` in the pair file are always kept, and flagged "not in data" when
missing. Each corridor gets a stable id (slug of its name), a colour from a
fixed palette, and these stats:

* median duration,
* changes,
* frequency in the window,
* earliest departure and latest arrival.

Its map geometry is a simplified polyline through the real stops. None of the
feeds has `shapes.txt`.

### Fares

GTFS has no prices, so `fares.toml` holds hand-edited notes per corridor id,
plus a BahnCard note (discounts apply to the German share only).
`fares.FareProvider` is the seam for a live-price provider later; v1 calls no
private DB API.

## Adding a city pair

Create `pairs/<from>-<to>.toml` (see `pairs/praha-luneburg.toml`) with:

* `from` / `to`,
* `places` (most distinctive first),
* `search_via` stations,
* optional `expected` corridors.

Without a pair file the explorer uses the busiest long-distance places between
the two cities and no via searches.

## Tests

```sh
uv run poe test                        # unit + golden tests (no network)
uv run pytest -o addopts= -m integration   # needs fetch + build-db first
```

The router tests use a tiny synthetic GTFS feed (`tests/gtfs_fixture.py`)
covering:

* a change, and a missed connection,
* a slow direct train kept as a Pareto trade-off,
* a trip past midnight and the previous day's trip boarded after midnight,
* calendar removals and additions,
* a train split across two trips,
* a bus route that must be ignored.

The integration test routes Praha hl.n. → Lüneburg on the next Tuesday in the
feed window. It expects the 06:31 ComfortJet with one change in Hamburg Hbf,
and a journey using the direct ICE Nürnberg → Lüneburg.

## Known gaps

* The German feeds have no train numbers, so labels are line names:
  "RJ", "EC 27", "ICE 25", "RE25".
* Without `cz_czptt`, the regional-only corridor has to leave Praha on the
  ZVON lines via Mladá Boleslav: the German feeds lack Czech locals to Děčín.
* Stations in the same city are not linked by walking or metro transfers,
  e.g. Praha-Holešovice ↔ Praha hl.n.
