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


def build(out: Path, cfg: dict, from_dir: Path | None = None) -> int:
    gc = cfg.get("gallery") or {}
    days, max_clips = int(gc.get("days", 3)), int(gc.get("max_clips", 24))
    if out.exists():
        shutil.rmtree(out)
    (out / "c").mkdir(parents=True)
    tmp = out / "_tmp"
    tmp.mkdir()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pairs = cards_from_dir(from_dir) if from_dir else cards_from_releases(repo, days, tmp)
    pairs = [(c, a) for c, a in pairs if c.get("go") and c.get("file")]
    pairs.sort(key=lambda ca: _when(ca[0].get("made_at", "")), reverse=True)
    clips = []
    for card, assets in pairs[:max_clips]:
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
