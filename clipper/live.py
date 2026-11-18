"""Veille des lives : la chasse de campagne démarre quand le streamer lance son live.

Toutes les 15 minutes (workflow « Veille des lives »), un contrôle léger regarde si les
streamers en campagne sont en direct. Il déclenche une chasse :
  - dès le début du live ;
  - puis toutes les heures tant qu'il dure ;
  - puis toutes les heures pendant 6 heures après la fin (clips tardifs et rediffusion,
    dont l'outil tire lui-même ses clips).
L'état (en live ou non, dernière chasse) est gardé dans state/live.json.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from . import campaign, sources
from .config import ROOT
from .util import log

STATE = ROOT / "state" / "live.json"
EVERY = timedelta(minutes=55)        # une chasse par heure pendant le live (marge pour le retard de GitHub)
AFTER = timedelta(hours=6)           # et encore pendant 6 heures après la fin


def kick_live(slug: str) -> bool:
    data = sources._kick_get(f"https://kick.com/api/v2/channels/{slug}")
    ls = data.get("livestream")
    return bool(ls) and bool(ls.get("is_live", True))


def twitch_live(login: str) -> bool:
    data = sources._gql(f"query {{ user(login: {json.dumps(login.lower())}) {{ stream {{ id }} }} }}")
    return bool(((data.get("user") or {}).get("stream") or {}).get("id"))


def youtube_live(handle: str) -> bool | None:
    """En direct sur YouTube ? None si YouTube refuse de répondre aux serveurs GitHub."""
    from . import fetch
    from .util import run
    url = f"https://www.youtube.com/@{str(handle).lstrip('@')}/live"
    p = run(fetch._base() + ["-J", "--", url], check=False, quiet=True, timeout=180)
    if p.returncode == 0:
        try:
            return bool(json.loads(p.stdout or "{}").get("is_live"))
        except Exception:
            return None
    err = (p.stderr or "").lower()
    if "not currently live" in err or "will begin" in err or "premieres in" in err or "is not live" in err:
        return False
    return None


def status(s: dict) -> bool | None:
    """Le streamer est-il en direct ? None si on n'a pas pu savoir."""
    try:
        if s.get("kick"):
            return kick_live(str(s["kick"]))
        if s.get("twitch"):
            return twitch_live(str(s["twitch"]))
        if s.get("youtube"):
            return youtube_live(str(s["youtube"]))
    except Exception as exc:
        log(f"Veille : statut de {s.get('name')} illisible ({exc})")
    return None


def watched(streamers: list[dict], now: datetime) -> list[dict]:
    """Streamers à surveiller : campagne en cours et fenêtre de tournage ouverte (ou proche)."""
    out = []
    for s in streamers:
        camp = campaign.of(s)
        if not camp or campaign.ended(camp, now):
            continue
        lo, hi = campaign.window(camp)
        if lo and now < lo - timedelta(hours=2):
            continue
        if hi and now > hi + timedelta(hours=18):   # le live du jour autorisé est fini depuis longtemps
            continue
        out.append(s)
    return out


def _dt(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except Exception:
        return None


def decide(statuses: dict[str, bool | None], state: dict, now: datetime) -> tuple[list[tuple[str, str]], dict]:
    """Qui déclenche une chasse maintenant, et pourquoi. Retourne (déclenchements, nouvel état)."""
    triggers: list[tuple[str, str]] = []
    new = dict(state)
    for name, live in statuses.items():
        st = dict(state.get(name) or {})
        if live is None:          # statut inconnu : on ne touche à rien
            new[name] = st
            continue
        last, ended = _dt(st.get("last")), _dt(st.get("ended"))
        due = last is None or now - last >= EVERY
        reason = ""
        if live:
            if not st.get("live"):
                reason = "début du live"
            elif due:
                reason = "chasse de l'heure pendant le live"
            st["live"] = True
            st.pop("ended", None)
        else:
            if st.get("live"):
                reason, st["ended"] = "fin du live", now.isoformat(timespec="seconds")
            elif ended and now - ended <= AFTER and due:
                reason = "après le live : clips tardifs et rediffusion"
            st["live"] = False
        if reason:
            st["last"] = now.isoformat(timespec="seconds")
            triggers.append((name, reason))
        new[name] = st
    return triggers, new


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def run_watch(streamers: list[dict], now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    targets = watched(streamers, now)
    if not targets:
        log("Veille : aucune campagne à surveiller en ce moment.")
        _output("run", "false")
        return 0
    statuses = {s["name"]: status(s) for s in targets}
    for name, live in statuses.items():
        log(f"Veille : {name} {'EN LIVE' if live else 'hors ligne' if live is False else 'statut inconnu'}")
    triggers, state = decide(statuses, load_state(), now)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    for name, why in triggers:
        log(f"Veille : chasse lancée pour {name} ({why})")
    _output("run", "true" if triggers else "false")
    _summary(statuses, triggers)
    return 0


def _output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def _summary(statuses: dict, triggers: list) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## Veille des lives", ""]
    for name, live in statuses.items():
        lines.append(f"- {name} : {'🔴 en live' if live else '⚪ hors ligne' if live is False else '❔ inconnu'}")
    if triggers:
        lines += ["", "Chasse de campagne lancée : " + ", ".join(f"{n} ({w})" for n, w in triggers)]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
