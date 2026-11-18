"""Coupes dynamiques : retirer les temps morts à l'intérieur d'un clip.

L'IA regarde l'extrait et propose des passages à enlever (attente, hésitations, répétitions,
moments où il ne se passe rien). On les cale sur les mots pour ne jamais couper une syllabe,
on garde des morceaux assez longs pour rester lisibles, et on renonce si le clip devient
trop court ou perd l'essentiel. Le montage enchaîne ensuite les morceaux gardés.
"""
from __future__ import annotations

from .util import log

MIN_CUT_S = 0.35     # en dessous, la coupe ne se voit pas et casse le rythme pour rien
MIN_PIECE_S = 0.9    # un morceau gardé plus court passe pour un bug


def _snap(a: float, b: float, words: list[dict]) -> tuple[float, float]:
    """Repousse les bords d'une coupe hors des mots : on ne coupe jamais au milieu d'un mot."""
    for w in words:
        if w["s"] < a < w["e"]:
            a = w["e"]
        if w["s"] < b < w["e"]:
            b = w["s"]
    return a, b


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 0.05:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def keep_segments(start: float, end: float, cuts: list[tuple[float, float]],
                  min_piece: float = MIN_PIECE_S) -> list[tuple[float, float]]:
    """Morceaux gardés de [start, end] une fois les coupes retirées, dans l'ordre."""
    segs: list[tuple[float, float]] = []
    t = start
    for a, b in _merge([(max(a, start), min(b, end)) for a, b in cuts if b > start and a < end]):
        if a - t >= min_piece:
            segs.append((t, a))
        t = max(t, b)
    if end - t >= min_piece:
        segs.append((t, end))
    return segs or [(start, end)]


def remap(t: float, segs: list[tuple[float, float]]) -> float | None:
    """Temps dans la source → temps dans le montage (None si t tombe dans une coupe)."""
    before = 0.0
    for s, e in segs:
        if s <= t <= e:
            return before + (t - s)
        before += e - s
    return None


def from_ai(raw, extract_start: float, start: float, end: float, words: list[dict],
            ed: dict) -> list[tuple[float, float]]:
    """Coupes proposées par l'IA (secondes dans l'extrait) → plages à retirer, en temps source."""
    if not ed.get("jump_cuts", True) or not isinstance(raw, list):
        return []
    found: list[tuple[float, float]] = []
    for item in raw:
        try:
            a, b = sorted((float(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(extract_start + a, start), min(extract_start + b, end)
        a, b = _snap(a, b, words)
        if b - a >= MIN_CUT_S:
            found.append((a, b))
    found = _merge(found)[: int(ed.get("max_cuts", 6))]
    if not found:
        return []
    dur = end - start
    kept = sum(e - s for s, e in keep_segments(start, end, found))
    # Garde-fous : le clip doit rester un vrai moment, pas une suite de flashs.
    if kept < max(float(ed.get("min_after_cuts_s", 10)), dur * float(ed.get("min_keep_ratio", 0.55))):
        log(f"Coupes de l'IA ignorées : il ne resterait que {kept:.1f} s sur {dur:.1f} s.")
        return []
    log(f"Coupes dynamiques : {len(found)} temps mort(s) retiré(s), {dur:.1f} s → {kept:.1f} s.")
    return found
