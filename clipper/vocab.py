"""Vocabulaire à reconnaître : argot, noms propres, termes de jeu (fichier vocabulaire.yaml)."""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import ROOT


def load_vocab(path: str | Path | None = None) -> list[str]:
    p = Path(path) if path else ROOT / "vocabulaire.yaml"
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    words: list[str] = []
    for value in data.values():
        if isinstance(value, list):
            words.extend(str(v).strip() for v in value if str(v).strip())
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def for_clip(base: list[str], *extra: str) -> list[str]:
    """Vocabulaire du fichier, plus les noms propres connus pour ce clip (streamer, titre…)."""
    out = [e.strip().lstrip("@") for e in extra if e and e.strip()]
    return out + [w for w in base if w.lower() not in {o.lower() for o in out}]
