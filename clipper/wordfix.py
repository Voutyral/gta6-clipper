"""Remettre une transcription corrigée sur les temps des mots d'origine.

La reconnaissance vocale donne des temps précis mais se trompe sur l'argot et les noms.
Gemini, qui écoute le clip, donne le bon texte mais sans temps fiables. On aligne les deux :
les mots identiques gardent leurs temps, les mots corrigés reprennent ceux des mots qu'ils
remplacent, les mots ajoutés se glissent dans les silences voisins.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .captions import display_word, normalize_words


# Personne ne dit plus de 10 mots par seconde : une correction qui demanderait d'aller plus vite
# vient en général d'une autre voix (un autre joueur) que la reconnaissance n'a pas entendue.
MIN_WORD_S = 0.1


def key(word: str) -> str:
    """Forme de comparaison : minuscules, sans accents ni ponctuation."""
    w = unicodedata.normalize("NFKD", word.lower())
    w = "".join(ch for ch in w if not unicodedata.combining(ch))
    return re.sub(r"[^\w']", "", w).replace("’", "'")


def _spread(tokens: list[str], start: float, end: float) -> list[dict]:
    """Répartit des mots sur [start, end] selon leur longueur."""
    end = max(end, start + 0.08 * len(tokens))
    weights = [len(t) + 2 for t in tokens]   # un petit mot (« de ») reste lisible
    total = float(sum(weights))
    out, t = [], start
    for tok, wgt in zip(tokens, weights, strict=True):
        d = (end - start) * wgt / total
        out.append({"raw": tok, "s": round(t, 3), "e": round(t + d, 3), "p": 0.8})
        t += d
    return out


def align(words: list[dict], corrected: str,
          min_ratio: float = 0.45) -> tuple[list[dict], float, list[tuple[str, str]]]:
    """Retourne (mots corrigés horodatés, ressemblance entre 0 et 1, corrections « avant → après »).

    Si la correction ressemble trop peu à ce qui a été entendu (moins de `min_ratio`),
    elle est jugée douteuse et les mots d'origine sont gardés.
    """
    tokens = [t for t in (corrected or "").split() if key(t)]
    if not words or not tokens:
        return words, 0.0, []
    a = [key(w["raw"]) for w in words]
    b = [key(t) for t in tokens]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    ratio = sm.ratio()
    if ratio < min_ratio:
        return words, ratio, []
    out: list[dict] = []
    edits: list[tuple[str, str]] = []

    def said(i1: int, i2: int) -> str:
        return " ".join(w["raw"] for w in words[i1:i2])
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out.append(dict(words[i1 + k], raw=tokens[j1 + k]))
                if display_word(words[i1 + k]["raw"]) != display_word(tokens[j1 + k]):   # accent corrigé
                    edits.append((words[i1 + k]["raw"], tokens[j1 + k]))
        elif tag == "replace":
            n = j2 - j1
            start, natural_end = words[i1]["s"], words[i2 - 1]["e"]
            limit = words[i2]["s"] if i2 < len(words) else natural_end + 0.6
            if limit - start < MIN_WORD_S * n:
                out.extend(dict(w) for w in words[i1:i2])   # ne tient pas dans le temps : on garde l'entendu
                continue
            out.extend(_spread(tokens[j1:j2], start, min(limit, max(natural_end, start + 0.2 * n))))
            edits.append((said(i1, i2), " ".join(tokens[j1:j2])))
        elif tag == "insert":
            prev = out[-1] if out else None
            nxt = words[i1]["s"] if i1 < len(words) else None
            n = j2 - j1
            if prev is not None and nxt is not None:
                if nxt - prev["e"] > 0.45 * n + 1.0:
                    continue   # loin de tout mot entendu : on ne devine pas où les placer
                start, end = prev["e"], nxt
                if end - start < 0.12 * n:          # pas de place : on prend sur le mot précédent
                    start = max(prev["s"] + 0.05, end - 0.18 * n)
                    if end - start < MIN_WORD_S * n:
                        continue                    # même ainsi, impossible à dire dans ce temps
                    prev["e"] = start
            elif prev is not None:
                start, end = prev["e"], prev["e"] + 0.25 * n
            else:
                end = nxt if nxt is not None else 0.25 * n
                start = max(0.0, end - 0.25 * n)
                if end - start < MIN_WORD_S * n:
                    continue                        # le clip commence déjà sur le premier mot entendu
            out.extend(_spread(tokens[j1:j2], start, end))
            edits.append(("", " ".join(tokens[j1:j2])))
        else:
            # « delete » : des mots entendus que l'IA juge absents (souvent inventés) disparaissent.
            edits.append((said(i1, i2), ""))
    # Mots nettoyés, temps croissants (le temps est déjà celui de la source : pas de plafond).
    return normalize_words(out, 1e9), ratio, edits


def describe(edits: list[tuple[str, str]], limit: int = 12) -> str:
    """« la viande → l'avion de », « + à », « − dessus » : lisible dans le journal du lancement."""
    parts = []
    for old, new in edits[:limit]:
        parts.append(f"« {old} » → « {new} »" if old and new else (f"+ « {new} »" if new else f"− « {old} »"))
    more = len(edits) - limit
    return ", ".join(parts) + (f", et {more} autre(s)" if more > 0 else "")
