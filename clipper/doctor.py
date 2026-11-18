"""Bilan de santé : vérifie en une fois les clés, les services et, au besoin, des liens.

    python -m clipper doctor --request "https://www.twitch.tv/videos/123"
"""
from __future__ import annotations

import os
import shutil
import subprocess


from . import ai, deliver, fetch, youtube
from .request import URL_RE
from .util import fmt_ts, log


def _ais(cfg: dict) -> list[str]:
    out = []
    if os.environ.get("GEMINI_API_KEY"):
        ok = ai._gemini(cfg, [{"text": "Réponds par le seul mot : bonjour"}], False)
        out.append(f"✅ Gemini : répond (modèle {ai._state['gemini_model']})" if ok
                   else "❌ Gemini : la clé ne répond pas (détail dans les logs)")
    else:
        out.append("⚪ Gemini : pas de clé (secret `GEMINI_API_KEY`)")
    if os.environ.get("ANTHROPIC_API_KEY"):
        ok = ai._claude(cfg, "Réponds en un mot.", "bonjour", None)
        out.append("✅ Claude : répond" if ok else "❌ Claude : la clé ne répond pas (détail dans les logs)")
    if os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_ACTIONS"):
        ok = ai._github(cfg, "Réponds en un mot.", "bonjour", None)
        out.append("✅ IA gratuite de GitHub : répond" if ok
                   else "⚪ IA gratuite de GitHub : indisponible sur ce dépôt (sans importance si Gemini répond)")
    return out


def _youtube() -> str:
    keys = ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    have = [k for k in keys if os.environ.get(k, "").strip()]
    if not have:
        return "⚪ Envoi automatique YouTube : non configuré (option)"
    if len(have) < 3:
        return f"🟡 Envoi automatique YouTube : il manque {', '.join(k for k in keys if k not in have)}"
    token, err = youtube._access_token()
    return "✅ Envoi automatique YouTube : connexion acceptée" if token else f"❌ Envoi automatique YouTube : {err}"


def _tools() -> str:
    try:
        ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
        ff = " ".join(ff.split()[:3])
    except Exception:
        ff = "ffmpeg absent"
    try:
        import yt_dlp
        yd = "yt-dlp " + yt_dlp.version.__version__
    except Exception:
        yd = "yt-dlp absent"
    try:
        import faster_whisper
        fw = "faster-whisper " + faster_whisper.__version__
    except Exception:
        fw = "faster-whisper absent"
    return f"🧰 Outils : {ff}, {yd}, {fw}, deno {'présent' if shutil.which('deno') else 'absent'}"


def _describe(info: dict) -> str:
    dur = fmt_ts(info["duration"]) if info.get("duration") else "durée inconnue"
    cats = ", ".join(str(c) for c in info.get("categories") or []) or "catégorie inconnue"
    return f"« {info.get('title') or '?'} », chaîne {info.get('channel') or '?'}, {dur}, {cats}"


def _link(url: str) -> list[str]:
    out = []
    try:
        if "twitch.tv" in url and "/clips" in url:
            entries = fetch.list_entries(url, 3)
            out.append(f"✅ Liste lisible : {len(entries)} clip(s) vus sur {url}")
            for e in entries[:3]:
                out.append(f"   · {e.get('view_count')} vues, {e.get('duration')} s, {e.get('title')}")
            first = next((e.get("url") or e.get("webpage_url") for e in entries), None)
            if first:
                out.append(f"✅ Clip lisible : {_describe(fetch.probe(first))}")
        else:
            out.append(f"✅ Lien lisible : {_describe(fetch.probe(url))}")
    except fetch.FetchError as exc:
        out.append(f"❌ {url} : {exc} {exc.hint}")
    return out


def run_doctor(text: str, cfg: dict) -> int:
    lines = ["## Bilan de santé du clippeur", ""] + _ais(cfg) + [_youtube(), _tools()]
    for url in [u.rstrip(".,;:!?") for u in URL_RE.findall(text or "")][:5]:
        lines.extend(_link(url))
    report = "\n".join(lines)
    log(report)
    deliver.step_summary(report.replace("\n", "  \n"))
    deliver.issue_comment(report.replace("\n", "  \n"))
    return 0
