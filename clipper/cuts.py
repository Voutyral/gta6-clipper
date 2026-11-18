"""Coupes dynamiques : retirer les temps morts à l'intérieur d'un clip.

L'IA regarde l'extrait et propose des passages à enlever (attente, hésitations, répétitions,
moments où il ne se passe rien). On les cale sur les mots pour ne jamais couper une syllabe,
on garde des morceaux assez longs pour rester lisibles, et on renonce si le clip devient
trop court ou perd l'essentiel. Le montage enchaîne ensuite les morceaux gardés.
"""
from __future__ import annotations

from itertools import pairwise

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
    where = ", ".join(f"à {a - start:.1f} s (−{b - a:.1f} s)" for a, b in found)
    log(f"Coupes dynamiques : {len(found)} temps mort(s) retiré(s), {dur:.1f} s → {kept:.1f} s ({where}).")
    return found


# ---------------------------------------------------------------- Temps morts retirés d'office

def tighten_on(ed: dict, preset: dict | None) -> bool:
    """Resserrage automatique voulu ? Désactivable dans config.yaml ou par streamer (campagne)."""
    want = (preset or {}).get("tighten", ed.get("tighten", True))
    return str(want).strip().lower() not in ("off", "false", "no", "non", "0", "none")


def _pieces_ok(start: float, end: float, cuts: list[tuple[float, float]]) -> bool:
    """Vrai si aucun morceau gardé n'est trop court (il serait jeté, avec les mots qu'il contient)."""
    merged = _merge([(max(a, start), min(b, end)) for a, b in cuts if b > start and a < end])
    kept = sum(e - s for s, e in keep_segments(start, end, merged))
    return abs(kept - ((end - start) - sum(b - a for a, b in merged))) < 0.02


def dead_gaps(words: list[dict], start: float, end: float, db, motion, *, hop: float = 0.05,
              fps: float = 10.0, ed: dict | None = None) -> list[tuple[float, float]]:
    """Silences à raccourcir : entre deux phrases, son calme et image presque immobile.

    `db` : niveau sonore tous les `hop` s depuis `start` ; `motion` : mouvement `fps` fois par seconde.
    Chaque silence garde un petit souffle (`tighten_leave_s`) pour que la coupe ne hache pas la voix.
    Jamais pendant l'action (image qui bouge, son fort) : c'est la règle du monteur qui coupe
    « là où il ne se passe rien ».
    """
    import numpy as np

    ed = ed or {}
    gap_s = float(ed.get("tighten_gap_s", 0.75))
    leave = float(ed.get("tighten_leave_s", 0.25))
    quiet_db = float(ed.get("tighten_quiet_db", 9.0))
    calm = float(ed.get("tighten_motion", 6.0))
    ws = sorted((w for w in words if w["s"] >= start - 0.05 and w["e"] <= end + 0.05), key=lambda w: w["s"])
    db = np.asarray(db, dtype=np.float32)
    motion = np.asarray(motion, dtype=np.float32)
    if len(ws) < 3 or len(db) < 10:
        return []

    def idx(t: float, rate: float, n: int) -> int:
        return int(min(max(0, round((t - start) * rate)), max(0, n - 1)))

    speech = [db[idx(w["s"], 1 / hop, len(db)):idx(w["e"], 1 / hop, len(db)) + 1] for w in ws]
    speech = np.concatenate([x for x in speech if len(x)]) if any(len(x) for x in speech) else db
    voice = float(np.median(speech))
    if voice < -45:        # voix trop faible pour juger ce qui est calme
        return []
    base_motion = float(np.median(motion)) if len(motion) else 0.0
    found: list[tuple[float, float]] = []
    spans = [(start, ws[0]["s"] - 0.12)] + [(a["e"], b["s"]) for a, b in pairwise(ws)]
    for k, (g0, g1) in enumerate(spans):
        if g1 - g0 < gap_s:
            continue
        a, b = (g0, g1) if k == 0 else (g0 + leave / 2, g1 - leave / 2)
        if b - a < MIN_CUT_S:
            continue
        loud = db[idx(a, 1 / hop, len(db)):idx(b, 1 / hop, len(db)) + 1]
        if not len(loud) or float(np.percentile(loud, 95)) > voice - quiet_db:
            continue       # son du jeu ou réaction : ce n'est pas un temps mort
        if len(motion):
            moving = motion[idx(a, fps, len(motion)):idx(b, fps, len(motion)) + 1]
            m = float(np.mean(moving)) if len(moving) else 0.0
            if m > calm or m > max(1.5 * base_motion, base_motion + 2.0):
                continue   # il se passe quelque chose à l'image (action, ou plus que d'habitude)
        found.append((round(a, 3), round(b, 3)))
    return found


def tighten(src, start: float, end: float, words: list[dict], ed: dict,
            fixed: list[tuple[float, float]] | None = None) -> list[tuple[float, float]]:
    """Ajoute aux coupes de l'IA (`fixed`) les temps morts trouvés au son et à l'image.

    Les plus longs d'abord, sans morceau trop court entre deux coupes, et jamais plus d'une
    part `tighten_max_share` du clip retirée de cette façon.
    """
    from . import detect

    fixed = list(fixed or [])
    dur = end - start
    if dur < 8:
        return fixed
    try:
        db = detect.energy_profile(src, hop=0.05, ss=start, dur=dur)
        motion = detect.motion_profile(src, start, dur)
    except Exception as exc:  # noqa: BLE001  (mesure impossible : on garde les coupes de l'IA)
        log(f"Resserrage ignoré : {exc}")
        return fixed
    gaps = dead_gaps(words, start, end, db, motion, ed=ed)
    budget = dur * float(ed.get("tighten_max_share", 0.25))
    limit = int(ed.get("tighten_max", 8))
    chosen: list[tuple[float, float]] = []
    for a, b in sorted(gaps, key=lambda g: g[1] - g[0], reverse=True):
        if len(chosen) >= limit or sum(y - x for x, y in chosen) + (b - a) > budget:
            continue
        if any(a < y and b > x for x, y in fixed + chosen):
            continue
        if _pieces_ok(start, end, fixed + chosen + [(a, b)]):
            chosen.append((a, b))
    if not chosen:
        return fixed
    out = _merge(fixed + chosen)
    kept = sum(e - s for s, e in keep_segments(start, end, out))
    if kept < max(float(ed.get("min_after_cuts_s", 10)), dur * float(ed.get("min_keep_ratio", 0.55))):
        return fixed
    log(f"Resserrage : {len(chosen)} silence(s) raccourci(s), −{sum(b - a for a, b in chosen):.1f} s "
        f"({', '.join(f'{a - start:.1f} s' for a, b in sorted(chosen))}).")
    return out
