"""Radar : chaque jour, les meilleurs clips Twitch des streamers autorisés.

Seuls les streamers de streamers.yaml avec `auto: true` sont suivis : ce sont ceux
dont tu as rejoint la campagne ou obtenu l'accord.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import deliver, fetch
from .config import ROOT
from .pipeline import run_requests
from .request import ClipRequest
from .util import log

SEEN = ROOT / "state" / "seen.json"


def _load_seen() -> dict:
    try:
        return json.loads(SEEN.read_text())
    except Exception:
        return {}


def _save_seen(seen: dict) -> None:
    limit = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    SEEN.parent.mkdir(exist_ok=True)
    SEEN.write_text(json.dumps({k: v for k, v in seen.items() if v >= limit}, indent=0))


def _on_topic(entry: dict, keywords: list[str]) -> bool:
    text = " ".join([str(entry.get("title") or "")] + [str(c) for c in entry.get("categories") or []]).lower()
    return any(k.lower() in text for k in keywords)


def pick_clips(streamers: list[dict], cfg: dict, seen: dict) -> list[tuple[dict, dict, str]]:
    rc = cfg["radar"]
    picks: list[tuple[dict, dict, str]] = []
    for s in streamers:
        if not (s.get("auto") and s.get("twitch")):
            continue
        url = f"https://www.twitch.tv/{s['twitch']}/clips?filter=clips&range={rc.get('range', '24hr')}"
        try:
            entries = fetch.list_entries(url, int(rc.get("scan_limit", 30)))
        except fetch.FetchError as exc:
            log(f"Radar : {s['name']} illisible ({exc} {exc.hint})")
            continue
        min_views = int(s.get("min_views", rc.get("min_views", 300)))
        good = [e for e in entries
                if str(e.get("id") or "") and str(e.get("id")) not in seen
                and (e.get("view_count") or 0) >= min_views and (e.get("duration") or 0) <= 61
                and (e.get("url") or e.get("webpage_url"))]
        if rc.get("require_keywords"):
            good = [e for e in good if _on_topic(e, list(rc.get("keywords") or []))]
        good.sort(key=lambda e: e.get("view_count") or 0, reverse=True)
        for e in good[: int(rc.get("max_per_streamer", 2))]:
            picks.append((s, e, e.get("url") or e.get("webpage_url")))
    picks.sort(key=lambda t: t[1].get("view_count") or 0, reverse=True)
    return picks[: int(rc.get("max_per_run", 4))]


def run_radar(cfg: dict, streamers: list[dict], base: Path, outdir: Path, dry_run: bool = False) -> int:
    if not cfg["radar"].get("enabled", True):
        log("Radar désactivé dans config.yaml.")
        return 0
    if not any(s.get("auto") and s.get("twitch") for s in streamers):
        log("Radar : aucun streamer avec « auto: true » et un pseudo Twitch dans streamers.yaml.")
        return 0
    seen = _load_seen()
    picks = pick_clips(streamers, cfg, seen)
    if not picks:
        log("Radar : rien de nouveau.")
        return 0
    for s, e, url in picks:
        log(f"Radar : {s['name']} · {e.get('view_count')} vues · {e.get('title')} · {url}")
    if dry_run:
        return 0
    reqs = [ClipRequest(url=url, credit=s.get("credit") or "@" + str(s["twitch"]), count=1,
                        layout=s.get("layout"), raw="radar") for s, e, url in picks]
    results, errors = run_requests(reqs, cfg, streamers, base, outdir)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    failed = {u for u, _, _ in errors}
    for _s, e, url in picks:
        if url not in failed:
            seen[str(e["id"])] = today
    _save_seen(seen)
    deliver.step_summary("## Radar du jour\n\n" + deliver.markdown_report(results, errors))
    deliver.cleanup_releases(int(cfg["delivery"].get("keep_days", 7)))
    return 0
