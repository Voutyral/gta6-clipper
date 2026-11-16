"""Livraison : Telegram, lien de téléchargement GitHub, réponse sur la demande."""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from .config import ROOT
from .util import CmdError, log, run

STATE = ROOT / "state"


def in_actions() -> bool:
    return bool(os.environ.get("GITHUB_ACTIONS") and os.environ.get("GITHUB_REPOSITORY"))


# ---------------------------------------------------------------- Telegram

def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def telegram_chat_id() -> str:
    """Secret TELEGRAM_CHAT_ID, sinon discussion mémorisée, sinon la première personne
    qui a écrit au bot (appuie sur « Démarrer » dans ton bot avant le premier clip)."""
    cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if cid:
        return cid
    cache = STATE / "telegram_chat.json"
    if cache.exists():
        try:
            return str(json.loads(cache.read_text())["chat_id"])
        except Exception:
            pass
    if not _token():
        return ""
    try:
        r = requests.get(f"https://api.telegram.org/bot{_token()}/getUpdates",
                         params={"timeout": 0}, timeout=30).json()
        for upd in r.get("result", []):
            chat = (upd.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private" and chat.get("id"):
                STATE.mkdir(exist_ok=True)
                cache.write_text(json.dumps({"chat_id": chat["id"]}))
                return str(chat["id"])
    except Exception as exc:
        log(f"Telegram : lecture des messages impossible ({exc})")
    return ""


def telegram_ready() -> bool:
    return bool(_token() and telegram_chat_id())


def _tg(method: str, data: dict, files: dict | None = None) -> bool:
    try:
        r = requests.post(f"https://api.telegram.org/bot{_token()}/{method}", data=data,
                          files=files, timeout=900)
        out = r.json()
    except Exception as exc:
        log(f"Telegram {method} : {exc}")
        return False
    if not out.get("ok"):
        log(f"Telegram {method} : {out.get('description')}")
        return False
    return True


def send_text(text: str) -> bool:
    if not telegram_ready():
        return False
    return _tg("sendMessage", {"chat_id": telegram_chat_id(), "text": text[:4000],
                               "parse_mode": "HTML", "disable_web_page_preview": "true"})


def send_video(path: Path, caption: str, duration: float) -> bool:
    if not telegram_ready():
        return False
    with open(path, "rb") as f:
        return _tg("sendVideo", {
            "chat_id": telegram_chat_id(), "caption": caption[:1000], "parse_mode": "HTML",
            "supports_streaming": "true", "width": 1080, "height": 1920,
            "duration": int(round(duration))}, files={"video": (path.name, f, "video/mp4")})


# ---------------------------------------------------------------- GitHub

def release_upload(path: Path) -> str:
    """Dépose le clip dans une « release » du jour et retourne son lien de téléchargement."""
    if not in_actions():
        return ""
    now = datetime.now(timezone.utc)
    tag = f"clips-{now:%Y%m%d}"
    try:
        if run(["gh", "release", "view", tag], check=False, quiet=True).returncode != 0:
            run(["gh", "release", "create", tag, "--title", f"Clips du {now:%d/%m/%Y}", "--notes",
                 "Clips générés automatiquement. Supprimés au bout de quelques jours.",
                 "--latest=false"], quiet=True)
        run(["gh", "release", "upload", tag, str(path), "--clobber"], quiet=True, timeout=900)
    except CmdError as exc:
        log(f"Lien de téléchargement indisponible : {exc}")
        return ""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{os.environ['GITHUB_REPOSITORY']}/releases/download/{tag}/{quote(path.name)}"


def cleanup_releases(keep_days: int) -> None:
    if not in_actions():
        return
    try:
        p = run(["gh", "release", "list", "--limit", "100", "--json", "tagName,createdAt"], quiet=True)
        limit = datetime.now(timezone.utc) - timedelta(days=int(keep_days))
        for rel in json.loads(p.stdout or "[]"):
            tag = rel.get("tagName", "")
            if not tag.startswith("clips-"):
                continue
            created = datetime.fromisoformat(rel["createdAt"].replace("Z", "+00:00"))
            if created < limit:
                run(["gh", "release", "delete", tag, "--yes", "--cleanup-tag"], check=False, quiet=True)
    except Exception as exc:
        log(f"Nettoyage des anciens clips ignoré : {exc}")


def issue_comment(body: str) -> None:
    num = os.environ.get("ISSUE_NUMBER", "").strip()
    if not (in_actions() and num):
        return
    tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "comment.md"
    tmp.write_text(body, encoding="utf-8")
    run(["gh", "issue", "comment", num, "--body-file", str(tmp)], check=False, quiet=True)


def step_summary(body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(body + "\n")


# ---------------------------------------------------------------- Messages

def _status(res) -> str:
    return "✅ GO" if res.go else "⚠️ NO-GO"


def telegram_caption(res) -> str:
    return (f"<b>{html.escape(res.title)}</b>\n{_status(res)} · {res.duration:.0f} s · "
            f"{res.size_mb:.0f} Mo")


def telegram_details(res) -> str:
    lines = ["<b>Titre</b> (touche pour copier)", f"<code>{html.escape(res.title)}</code>", "",
             "<b>Description</b>", f"<code>{html.escape(res.description)}</code>"]
    if res.tags:
        lines += ["", "<b>Tags</b>", f"<code>{html.escape(', '.join(res.tags))}</code>"]
    for p in res.problems:
        lines.append(f"\n⛔ {html.escape(p)}")
    for n in res.notes:
        lines.append(f"\n⚠️ {html.escape(n)}")
    if res.paid:
        lines.append("\n💶 Campagne rémunérée : coche « promotion payante » à la publication.")
    if res.yt.get("url"):
        lines.append(f"\n▶️ YouTube ({html.escape(res.yt.get('privacy', '?'))}) : {res.yt['url']}")
    elif res.yt.get("error"):
        lines.append(f"\n▶️ Envoi YouTube échoué : {html.escape(res.yt['error'])}")
    if res.link:
        lines.append(f"\n⬇️ {res.link}")
    return "\n".join(lines)


def markdown_report(results: list, errors: list) -> str:
    out = []
    for res in results:
        out.append(f"### {_status(res)} · {res.title}")
        out.append(f"- Durée {res.duration:.0f} s, {res.size_mb:.0f} Mo, accroche « {res.hook} »")
        if res.link:
            out.append(f"- [Télécharger le clip]({res.link})")
        if res.telegram:
            out.append("- Envoyé sur Telegram")
        if res.yt.get("url"):
            out.append(f"- YouTube ({res.yt.get('privacy', '?')}) : {res.yt['url']}")
        elif res.yt.get("error"):
            out.append(f"- Envoi YouTube échoué : {res.yt['error']}")
        for p in res.problems:
            out.append(f"- ⛔ {p}")
        for n in res.notes:
            out.append(f"- ⚠️ {n}")
        out.append("\n<details><summary>Description à coller</summary>\n\n```\n"
                   + res.description + "\n```\n</details>\n")
    for url, message, hint in errors:
        out.append(f"### ❌ {url}\n{message}" + (f"\n\n{hint}" if hint else "") + "\n")
    return "\n".join(out) if out else "Rien à traiter."
