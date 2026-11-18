"""Livraison : lien de téléchargement GitHub et réponse sur la demande (issue)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .config import ROOT
from .util import CmdError, log, run

STATE = ROOT / "state"


def in_actions() -> bool:
    return bool(os.environ.get("GITHUB_ACTIONS") and os.environ.get("GITHUB_REPOSITORY"))


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


def open_issue(title: str, body: str, *, label: str = "", close_previous: bool = False) -> str:
    """Ouvre une issue (le téléphone reçoit une notification de l'app GitHub).

    Avec `close_previous`, les anciennes issues de la même étiquette sont fermées : une seule reste ouverte.
    """
    if not in_actions():
        return ""
    if label:
        run(["gh", "label", "create", label, "--color", "F9D71C", "--force",
             "--description", "Créée par le clippeur"], check=False, quiet=True)
        if close_previous:
            p = run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/issues?state=open&labels={label}&per_page=50",
                     "--jq", ".[].number"], check=False, quiet=True)
            for num in (p.stdout or "").split():
                run(["gh", "issue", "close", num, "--reason", "completed"], check=False, quiet=True)
    tmp = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "issue.md"
    tmp.write_text(body, encoding="utf-8")
    cmd = ["gh", "issue", "create", "--title", title, "--body-file", str(tmp)]
    if label:
        cmd += ["--label", label]
    p = run(cmd, check=False, quiet=True)
    return (p.stdout or "").strip()


_PAGES: dict = {}


def pages_url() -> str:
    """Adresse de la galerie si GitHub Pages est activé sur le dépôt, sinon chaîne vide."""
    if "url" not in _PAGES:
        url = ""
        if in_actions():
            p = run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/pages", "--jq", ".html_url"],
                    check=False, quiet=True)
            url = (p.stdout or "").strip() if p.returncode == 0 else ""
        _PAGES["url"] = url
    return _PAGES["url"]


def step_summary(body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(body + "\n")


# ---------------------------------------------------------------- Messages

def _status(res) -> str:
    return "✅ GO" if res.go else "⚠️ NO-GO"


def markdown_report(results: list, errors: list, gallery: bool = False) -> str:
    out = []
    if gallery and results and pages_url():
        out += [f"## ▶️ [Ouvrir la galerie]({pages_url()})", ""]
    for res in results:
        out.append(f"### {_status(res)} · {res.title}")
        out.append(f"- Durée {res.duration:.0f} s, {res.size_mb:.0f} Mo, accroche « {res.hook} »")
        f = res.facts
        out.append(f"- Montage : {f.get('layout')}, aperçu {'oui' if f.get('teaser') else 'non'}, "
                   f"{f.get('punches', 0)} impact(s), {f.get('captions', 0)} mots sous-titrés, "
                   f"IA : {res.provider or 'aucune'}")
        if res.link:
            out.append(f"- [Télécharger le clip]({res.link})")
        m = getattr(res, "meta", {}) or {}
        if m.get("ai_score") is not None:
            out.append(f"- Note de l'IA : {m['ai_score']:g}/10" + (f" · {m['what']}" if m.get("what") else ""))
        if m.get("found_why"):
            out.append(f"- Trouvé par la chasse : {m['found_why']}")
        if res.yt.get("url"):
            out.append(f"- YouTube ({res.yt.get('privacy', '?')}) : {res.yt['url']}")
        elif res.yt.get("error"):
            out.append(f"- Envoi YouTube échoué : {res.yt['error']}")
        for p in res.problems:
            out.append(f"- ⛔ {p}")
        for n in res.notes:
            out.append(f"- ⚠️ {n}")
        if res.paid:
            out.append("- 💶 Campagne rémunérée : coche « promotion payante » à la publication")
        out.append("\n<details><summary>Description à coller</summary>\n\n```\n"
                   + res.description + "\n```\n</details>\n")
    for url, message, hint in errors:
        out.append(f"### ❌ {url}\n{message}" + (f"\n\n{hint}" if hint else "") + "\n")
    return "\n".join(out) if out else "Rien à traiter."
