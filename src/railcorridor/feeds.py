"""Download GTFS feeds and cache them as date-stamped zips under ``data/raw``."""

from __future__ import annotations

import datetime as dt
import re
import shutil
import urllib.request
from pathlib import Path

from railcorridor.config import FEEDS, RAW_DIR

_STAMP = re.compile(r"^(?P<name>[a-z0-9_]+)_(?P<date>\d{8})\.zip$")


def cached_zips(name: str, raw_dir: Path = RAW_DIR) -> list[Path]:
    """All cached zips for ``name``, oldest first."""
    if not raw_dir.exists():
        return []
    hits = []
    for p in raw_dir.iterdir():
        m = _STAMP.match(p.name)
        if m and m["name"] == name:
            hits.append(p)
    return sorted(hits)


def latest_zip(name: str, raw_dir: Path = RAW_DIR) -> Path | None:
    """Newest cached zip for ``name`` or None."""
    zips = cached_zips(name, raw_dir)
    return zips[-1] if zips else None


def fetch(
    name: str,
    *,
    raw_dir: Path = RAW_DIR,
    force: bool = False,
    today: dt.date | None = None,
) -> Path:
    """Download feed ``name`` unless today's copy is already cached."""
    spec = FEEDS[name]
    stamp = (today or dt.datetime.now(dt.UTC).date()).strftime("%Y%m%d")
    target = raw_dir / f"{name}_{stamp}.zip"
    if target.exists() and not force:
        return target
    raw_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    if not spec.url.startswith("https://"):
        msg = f"{name}: only https feed URLs are allowed"
        raise ValueError(msg)
    req = urllib.request.Request(
        spec.url, headers={"User-Agent": "railcorridor/0.1 (+GTFS explorer)"}
    )
    # scheme checked above
    with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as fh:  # nosec B310
        shutil.copyfileobj(resp, fh, length=1 << 20)
    tmp.replace(target)
    return target
