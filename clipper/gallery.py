"""Galerie : une page web (GitHub Pages) avec les clips prêts à poster.

Construite à partir des fiches JSON déposées à côté de chaque clip dans les releases
du dépôt, ou d'un dossier local pour les essais (--from-dir).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT
from .util import log, run

WEB = Path(__file__).resolve().parent / "web"
FONTS = ROOT / "assets" / "fonts"


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _download_asset(repo: str, asset_id: int, dest: Path) -> bool:
    with open(dest, "wb") as f:
        p = subprocess.run(["gh", "api", f"repos/{repo}/releases/assets/{asset_id}",
                            "-H", "Accept: application/octet-stream"], stdout=f, stderr=subprocess.PIPE)
    if p.returncode != 0 or dest.stat().st_size == 0:
        log(f"Galerie : téléchargement impossible de {dest.name} ({p.stderr.decode(errors='replace')[-200:]})")
        dest.unlink(missing_ok=True)
        return False
    return True


def cards_from_releases(repo: str, days: int, tmp: Path) -> list[tuple[dict, dict]]:
    """(fiche, assets par nom) pour chaque clip des releases « clips-AAAAMMJJ » récentes."""
    p = run(["gh", "api", f"repos/{repo}/releases?per_page=30"], quiet=True)
    limit = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for rel in json.loads(p.stdout or "[]"):
        if not str(rel.get("tag_name", "")).startswith("clips-") or _when(rel.get("created_at")) < limit:
            continue
        assets = {a["name"]: a for a in rel.get("assets") or []}
        for name, a in assets.items():
            if not name.endswith(".json"):
                continue
            dest = tmp / name
            if _download_asset(repo, a["id"], dest):
                try:
                    out.append((json.loads(dest.read_text(encoding="utf-8")), assets))
                except Exception:
                    continue
    return out


def cards_from_dir(folder: Path) -> list[tuple[dict, dict]]:
    out = []
    for side in sorted(folder.glob("*.json")):
        try:
            out.append((json.loads(side.read_text(encoding="utf-8")), {"_dir": str(folder)}))
        except Exception:
            continue
    return out


def keep_latest(cards: list[dict], hide: list[str] | None = None) -> list[dict]:
    """Clips à montrer : sans les clips masqués, et une seule version par source.

    Un même clip relancé plus tard (nouveaux sous-titres, par exemple) ne garde que sa version
    la plus récente. Plusieurs moments tirés d'une même source au même lancement restent tous.
    """
    hidden = {h.strip().rsplit(".", 1)[0] for h in (hide or []) if h.strip()}
    cards = [c for c in cards if str(c.get("file", "")).rsplit(".", 1)[0] not in hidden]
    run_of = lambda c: str(c.get("file", ""))[:15]   # « AAAAMMJJ-HHMMSS » : un lancement  # noqa: E731
    latest: dict[str, str] = {}
    for c in cards:
        src = c.get("source_url") or c.get("file")
        latest[src] = max(latest.get(src, ""), run_of(c))
    return [c for c in cards if run_of(c) == latest[c.get("source_url") or c.get("file")]]


def build(out: Path, cfg: dict, from_dir: Path | None = None) -> int:
    gc = cfg.get("gallery") or {}
    days, max_clips = int(gc.get("days", 7)), int(gc.get("max_clips", 40))
    # GitHub Pages refuse un site de plus de 1 Go : les vidéos les plus anciennes restent dehors.
    max_mb, used_mb = float(gc.get("max_mb", 900)), 0.0
    if out.exists():
        shutil.rmtree(out)
    (out / "c").mkdir(parents=True)
    tmp = out / "_tmp"
    tmp.mkdir()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pairs = cards_from_dir(from_dir) if from_dir else cards_from_releases(repo, days, tmp)
    pairs = [(c, a) for c, a in pairs if c.get("go") and c.get("file")]
    shown = {id(c) for c in keep_latest([c for c, _ in pairs], gc.get("hide"))}
    pairs = [(c, a) for c, a in pairs if id(c) in shown]
    pairs.sort(key=lambda ca: _when(ca[0].get("made_at", "")), reverse=True)
    clips = []
    for card, assets in pairs[:max_clips]:
        size = float(card.get("size_mb") or 30)
        if used_mb + size > max_mb:
            log(f"Galerie : place pleine ({used_mb:.0f} Mo), les clips plus anciens restent dans les releases.")
            break
        ok = True
        for key in ("file", "thumb"):
            name = card.get(key)
            if not name:
                continue
            dest = out / "c" / name
            if "_dir" in assets:
                src = Path(assets["_dir"]) / name
                ok = ok and src.exists()
                if src.exists():
                    shutil.copy(src, dest)
            elif name in assets:
                got = _download_asset(repo, assets[name]["id"], dest)
                ok = ok and (got or key == "thumb")
            elif key == "file":
                ok = False
        if not ok:
            continue
        card = dict(card, src=f"c/{card['file']}", poster=f"c/{card['thumb']}" if card.get("thumb") else "")
        clips.append(card)
        used_mb += size
    shutil.rmtree(tmp, ignore_errors=True)
    for f in WEB.iterdir():
        shutil.copy(f, out / f.name)
    (out / "fonts").mkdir()
    for f in ("Anton-Regular.ttf", "Poppins-ExtraBold.ttf"):
        if (FONTS / f).exists():
            shutil.copy(FONTS / f, out / "fonts" / f)
    (out / ".nojekyll").write_text("")
    data = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schedule_utc": gc.get("schedule_utc") or [], "clips": clips}
    (out / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Galerie : {len(clips)} clip(s) dans {out}")
    return len(clips)
