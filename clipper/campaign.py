"""Campagnes rémunérées (Content Rewards, Discord d'un streamer…) : leurs règles appliquées au clip.

Une campagne se déclare dans streamers.yaml, sous le streamer concerné (bloc `campaign:`).
La chasse ne prend alors que la source autorisée (le stream daté, le bon jeu), en priorité
sur tout le reste, et le montage suit le brief : langue, crédit, hashtags, alerte spoiler.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .util import game_key

SPOILER_HOOK = {"fr": "ALERTE SPOILER", "en": "SPOILER WARNING"}
SPOILER_LINE = {"fr": "⚠️ Spoiler : moment de l'histoire de GTA 6.",
                "en": "⚠️ Spoiler warning: GTA 6 story moment."}
OFF = ("off", "false", "no", "non", "0", "none")


def _dt(text) -> datetime | None:
    if isinstance(text, datetime):
        return text if text.tzinfo else text.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def of(preset: dict | None) -> dict:
    """Bloc `campaign` d'une fiche de streamers.yaml ({} s'il n'y en a pas)."""
    camp = (preset or {}).get("campaign")
    return camp if isinstance(camp, dict) else {}


def ended(camp: dict, now: datetime | None = None) -> bool:
    closes = _dt(camp.get("closes"))
    return bool(closes and (now or datetime.now(timezone.utc)) > closes)


def is_open(camp: dict, now: datetime | None = None) -> bool:
    """Publication permise : après l'ouverture et avant la date limite."""
    now = now or datetime.now(timezone.utc)
    opens = _dt(camp.get("opens"))
    return (opens is None or now >= opens) and not ended(camp, now)


def window(camp: dict) -> tuple[datetime | None, datetime | None]:
    return _dt(camp.get("footage_from")), _dt(camp.get("footage_until"))


def game_ok(camp: dict, game: str) -> bool:
    wanted = {game_key(g) for g in camp.get("games") or [] if str(g).strip()}
    return not wanted or game_key(game) in wanted


def stream_ids(found: list, camp: dict, streamer: str) -> set[str]:
    """Lives Kick qui ont produit au moins un clip pendant la fenêtre autorisée.

    Un live commencé le bon jour reste valable jusqu'à sa fin, même après minuit :
    ses clips plus tardifs sont reconnus par le numéro du live.
    """
    lo, hi = window(camp)
    out = set()
    for f in found:
        lsid = str((f.extra or {}).get("livestream_id") or "")
        if not lsid or f.streamer.lower() != streamer.lower() or not game_ok(camp, f.game):
            continue
        if (lo is None or f.created_at >= lo) and (hi is None or f.created_at < hi):
            out.add(lsid)
    return out


def footage_ok(f, camp: dict, lives: set[str]) -> tuple[bool, str]:
    """Le clip vient-il de la source autorisée par la campagne ? (oui/non, motif du refus)"""
    if not game_ok(camp, f.game):
        return False, "autre jeu que celui de la campagne"
    lo, hi = window(camp)
    if lo is not None and f.created_at < lo:
        return False, "avant le stream de la campagne"
    if hi is not None and f.created_at >= hi:
        lsid = str((f.extra or {}).get("livestream_id") or "")
        if not (camp.get("same_stream", True) and lsid and lsid in lives):
            return False, "après le stream de la campagne"
    return True, ""


def language(preset: dict | None) -> str:
    return str((preset or {}).get("language") or "").strip().lower()[:2]


def brief_for_ai(preset: dict | None) -> str:
    """Consignes de la campagne ajoutées à la demande faite à l'IA."""
    camp = of(preset)
    if not camp:
        return ""
    parts = [str(camp.get("brief") or "").strip()]
    if preset.get("story_ok"):
        parts.append("Moments d'histoire voulus (mort d'un personnage, révélation) : mets le risque "
                     "\"spoiler\" et ne révèle rien dans l'accroche ni dans le titre (aucun nom, aucun événement).")
    parts.append("Contenu sexuel explicite, nudité, insultes haineuses ou propos qui enfreignent les règles "
                 "de TikTok ou YouTube : risque \"explicite\" et note 2 au plus. Une blague d'adulte légère "
                 "passe si TikTok l'accepte.")
    parts.append("Bande-annonce, vidéo officielle, fuite ou autre stream que celui de la source : risque "
                 "\"bande_annonce\" et note 0.")
    parts.append("Garde sa réaction et la chute : ne les coupe jamais. Donne bien la position de sa webcam.")
    return "\n".join(p for p in parts if p)


def _tags(values) -> list[str]:
    out = []
    for t in values or []:
        t = str(t).strip()
        if t:
            out.append(t if t.startswith("#") else "#" + t)
    return out


def hashtags(preset: dict | None, seed: str = "") -> list[str]:
    """Hashtags exigés, plus quelques-uns pris dans `hashtags_mix` (ils changent d'un clip à l'autre)."""
    tags = _tags((preset or {}).get("hashtags"))
    mix = [t for t in _tags((preset or {}).get("hashtags_mix")) if t.lower() not in {x.lower() for x in tags}]
    if mix:
        n = min(len(mix), int((preset or {}).get("hashtags_mix_n", 3)))
        start = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(mix)
        tags += [mix[(start + k) % len(mix)] for k in range(n)]
    return tags


def cta(preset: dict | None) -> str:
    """Appel à l'action écrit à la fin du clip (« SUBSCRIBE TO HIM ON YOUTUBE »)."""
    return str((preset or {}).get("cta") or "").strip()


def caption_lines(preset: dict | None) -> list[str]:
    return [str(x).strip() for x in (preset or {}).get("caption_lines") or [] if str(x).strip()]


def spoiler_hook(lang: str) -> str:
    return SPOILER_HOOK.get(lang, SPOILER_HOOK["en"])


def spoiler_line(lang: str) -> str:
    return SPOILER_LINE.get(lang, SPOILER_LINE["en"])


def card(preset: dict | None) -> dict:
    """Ce que la galerie affiche sur la carte d'un clip de campagne."""
    camp = of(preset)
    if not camp:
        return {}
    return {"name": str(camp.get("name") or preset.get("name") or "Campagne"),
            "platforms": [str(p) for p in camp.get("platforms") or []],
            "opens": str(camp.get("opens") or ""), "closes": str(camp.get("closes") or ""),
            "submit_minutes": int(camp.get("submit_minutes") or 30),
            "reminders": [str(r) for r in camp.get("reminders") or [] if str(r).strip()]}


def off(value) -> bool:
    return str(value).strip().lower() in OFF
