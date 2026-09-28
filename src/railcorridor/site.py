"""Render ``out/<pair>/index.html``: one self-contained page, JSON and JS inlined."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from railcorridor.corridors import PALETTE, REGIONAL_COLOR


def _asset(name: str) -> str:
    return (resources.files("railcorridor") / name).read_text(encoding="utf-8")


def unify_colours(docs: list[dict]) -> list[dict]:
    """One colour per corridor id across all dates, in order of appearance."""
    order: list[dict] = []
    seen: set[str] = set()
    for d in docs:
        for c in d["corridors"]:
            if c["id"] not in seen:
                seen.add(c["id"])
                order.append(c)
    colours: dict[str, tuple[str, str]] = {}
    i = 0
    for c in order:
        if c["regional"]:
            colours[c["id"]] = REGIONAL_COLOR
        else:
            colours[c["id"]] = PALETTE[i % len(PALETTE)]
            i += 1
    for d in docs:
        for c in d["corridors"]:
            c["color"], c["color_dark"] = colours[c["id"]]
    return [
        {"id": c["id"], "color": colours[c["id"]][0], "color_dark": colours[c["id"]][1]}
        for c in order
    ]


def _script_json(obj: object) -> str:
    """JSON safe to embed inside <script> (no closing tags, no comment openers)."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("<!--", "<\\!--")


def build_site(pair_dir: Path) -> Path:
    """Render every ``<date>.json`` in ``pair_dir`` into ``index.html``."""
    files = sorted(pair_dir.glob("????-??-??.json"))
    if not files:
        msg = f"no <date>.json files in {pair_dir}; run query first"
        raise FileNotFoundError(msg)
    docs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    corridors = unify_colours(docs)
    rivers = json.loads(_asset("assets/rivers.json"))["rivers"]
    bundle = {
        "dates": [d["date"] for d in docs],
        "docs": {d["date"]: d for d in docs},
        "rivers": rivers,
    }
    env = Environment(
        loader=PackageLoader("railcorridor", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        keep_trailing_newline=True,
    )
    tpl = env.get_template("page.html.j2")
    latest = docs[-1]
    html = tpl.render(
        title=latest["pair"]["title"],
        corridors=corridors,
        data_json=Markup(_script_json(bundle)),
        app_js=Markup(_asset("static/app.js").replace("</script", "<\\/script")),
    )
    out = pair_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out
