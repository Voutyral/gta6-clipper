"""Contrôle GO / NO-GO : on ouvre le fichier rendu avant de le déclarer bon."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .util import probe_media

RISK_LABELS = {
    "cinematique": "cinématique du jeu isolée (Rockstar les fait retirer)",
    "spoiler": "révélation majeure de l'histoire (Rockstar les fait retirer)",
}
RISK_NOTES = {
    "violence_debut": "violence graphique dans les 7 premières secondes : pas de pub sur ce clip",
    "musique": "musique sous licence audible : risque de réclamation",
}


def mean_volume(path: str | Path) -> float | None:
    p = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path), "-vn", "-af",
                        "volumedetect", "-f", "null", "-"], capture_output=True, text=True,
                       errors="replace")
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr or "")
    return float(m.group(1)) if m else None


def first_frame_luma(path: str | Path) -> float | None:
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    if not p.stdout:
        return None
    return float(np.frombuffer(p.stdout, dtype=np.uint8).mean())


def verify(path: str | Path, *, facts: dict, risks: list[str], cfg: dict,
           rights_ok: bool) -> dict:
    """Retourne {"go": bool, "problems": [...], "notes": [...], "duration", "size_mb"}."""
    problems: list[str] = []
    notes: list[str] = []
    info = probe_media(path)
    if (info["width"], info["height"]) != (1080, 1920):
        problems.append(f"format {info['width']}x{info['height']} au lieu de 1080x1920")
    d = info["duration"]
    if d < 5:
        problems.append("clip de moins de 5 s")
    if d > 180:
        problems.append("plus de 3 min : ne sera pas classé en Short")
    elif d > 60:
        notes.append("plus de 60 s : vérifie la durée maximale de la campagne")
    if not info["has_audio"]:
        problems.append("pas de piste audio")
    size_mb = info["size"] / 1e6
    if size_mb > 49.5:
        problems.append(f"{size_mb:.0f} Mo : trop lourd pour un envoi Telegram")
    vol = mean_volume(path)
    if vol is not None and vol < -45:
        problems.append("son quasi muet")
    luma = first_frame_luma(path)
    if luma is not None and luma < 14:
        notes.append("première image noire : accroche visuelle faible")
    if cfg["captions"].get("enabled", True) and not facts.get("captions"):
        notes.append("aucune parole détectée : pas de sous-titres")
    if facts.get("fallback"):
        notes.append("montage simplifié (les effets ont été refusés par ffmpeg)")
    for r in risks or []:
        if r in RISK_LABELS:
            problems.append(RISK_LABELS[r])
        elif r in RISK_NOTES:
            notes.append(RISK_NOTES[r])
    if not rights_ok:
        notes.append("source absente de streamers.yaml : vérifie que tu as l'accord pour clipper")
    return {"go": not problems, "problems": problems, "notes": notes, "duration": d,
            "size_mb": size_mb}
