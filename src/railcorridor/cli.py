"""Command line interface."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import duckdb
import typer

from railcorridor import feeds as feeds_mod
from railcorridor.config import (
    DB_PATH,
    DEFAULT_FEEDS,
    FARES_PATH,
    FEEDS,
    OUT_DIR,
    RoutingConfig,
)
from railcorridor.explore import explore, load_pair, parse_window
from railcorridor.export import build_document, write_json
from railcorridor.fares import TomlFareProvider
from railcorridor.load import load_feed, loaded_feeds
from railcorridor.site import build_site
from railcorridor.stations import build_stations

app = typer.Typer(no_args_is_help=True, add_completion=False)

FeedOpt = Annotated[
    list[str] | None,
    typer.Option("--feed", "-f", help=f"Feed name(s): {', '.join(FEEDS)}"),
]


def _feeds(names: list[str] | None) -> list[str]:
    chosen = names or DEFAULT_FEEDS
    unknown = [n for n in chosen if n not in FEEDS]
    if unknown:
        raise typer.BadParameter(f"unknown feed(s): {', '.join(unknown)}")
    return chosen


@app.command()
def fetch(
    feed: FeedOpt = None,
    force: Annotated[bool, typer.Option(help="Re-download today's copy")] = False,
) -> None:
    """Download and cache feeds as data/raw/<feed>_<YYYYMMDD>.zip."""
    for name in _feeds(feed):
        path = feeds_mod.fetch(name, force=force)
        typer.echo(f"{name}: {path} ({path.stat().st_size / 1e6:.1f} MB)")


@app.command("build-db")
def build_db(
    feed: FeedOpt = None,
    db: Annotated[Path, typer.Option(help="DuckDB file")] = DB_PATH,
) -> None:
    """Load the newest cached zip of each feed into DuckDB and merge stations."""
    db.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db)) as con:
        for name in _feeds(feed):
            zp = feeds_mod.latest_zip(name)
            if zp is None:
                raise typer.BadParameter(f"{name}: no cached zip, run fetch first")
            typer.echo(f"loading {name} from {zp.name} ...")
            load_feed(con, name, zp)
            n = con.execute(f"SELECT count(*) FROM {name}.trips").fetchone()
            typer.echo(f"  {n[0] if n else 0} rail trips")
        names = loaded_feeds(con)
        n_st = build_stations(con, names)
        typer.echo(f"stations: {n_st} merged from feeds {', '.join(names)}")


@app.command()
def query(
    from_: Annotated[str, typer.Option("--from", help="Origin station")],
    to: Annotated[str, typer.Option("--to", help="Destination station")],
    date: Annotated[str, typer.Option(help="Service date YYYY-MM-DD")],
    depart_window: Annotated[
        str, typer.Option("--depart-window", help="HH:MM-HH:MM")
    ] = "04:00-11:00",
    both_directions: Annotated[
        bool, typer.Option("--both-directions/--one-direction")
    ] = True,
    regional: Annotated[
        bool,
        typer.Option(help="Also search a regional-only (Deutschlandticket) corridor"),
    ] = True,
    max_journeys: Annotated[int, typer.Option(help="Journeys per direction")] = 12,
    db: Annotated[Path, typer.Option(help="DuckDB file")] = DB_PATH,
    out: Annotated[Path, typer.Option(help="Output directory")] = OUT_DIR,
    fares: Annotated[Path, typer.Option(help="Fare notes TOML")] = FARES_PATH,
) -> None:
    """Compute journeys and corridors; writes out/<pair>/<date>.json."""
    day = dt.date.fromisoformat(date)
    cfg = RoutingConfig(max_journeys=max_journeys)
    pair = load_pair(from_, to)
    with duckdb.connect(str(db), read_only=True) as con:
        res = explore(
            con,
            pair,
            day,
            parse_window(depart_window),
            both_directions=both_directions,
            regional=regional,
            cfg=cfg,
            log=typer.echo,
        )
        doc = build_document(res, con, TomlFareProvider(fares))
    path = write_json(doc, out)
    for c in doc["corridors"]:
        typer.echo(f"  corridor {c['id']:<28} {c['name']}")
    for d, js in doc["journeys"].items():
        typer.echo(f"{d}:")
        for j in js:
            legs = " | ".join(
                f"{lg['label']} {lg['dep']}-{lg['arr']}" for lg in j["legs"]
            )
            typer.echo(
                f"  {j['dep']} -> {j['arr']} ({j['changes']}) [{j['corridor']}] {legs}"
            )
        for m in doc["missing"].get(d, []):
            typer.echo(f"  NOT IN DATA: {m['name']} ({m['reason']})")
    typer.echo(f"wrote {path}")


@app.command()
def site(
    pair: Annotated[
        str | None, typer.Argument(help="Pair id, e.g. praha-luneburg (default: all)")
    ] = None,
    out: Annotated[Path, typer.Option(help="Output directory")] = OUT_DIR,
) -> None:
    """Render out/<pair>/index.html from the JSON files written by query."""
    dirs = [out / pair] if pair else sorted(p for p in out.iterdir() if p.is_dir())
    for d in dirs:
        path = build_site(d)
        typer.echo(f"wrote {path} ({path.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    app()
