"""« Regarder, retenir, refaire » : tirer une fiche méthode d'une vidéo.

Lien YouTube + clé Gemini : Gemini lit la vidéo directement chez Google, sans téléchargement.
Autre source : téléchargement léger, transcription, planche d'images, puis le meilleur modèle disponible.
Les fiches sont rangées dans knowledge/ : c'est la matière première des skills.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import ai, deliver, fetch, transcribe
from .config import ROOT
from .render import contact_sheet, make_proxy
from .request import URL_RE
from .util import log, probe_media, slugify

KEYWORDS = ("apprends", "apprend", "regarde", "watch", "analyse")
KNOWLEDGE = ROOT / "knowledge"


def is_learn_request(text: str) -> bool:
    first = (text or "").strip().lower()
    return first.startswith(KEYWORDS)


def _is_youtube(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url, re.I))


def study(url: str, question: str, cfg: dict, work: Path) -> tuple[str, str, dict]:
    """Retourne (fiche Markdown, fournisseur, infos source)."""
    work.mkdir(parents=True, exist_ok=True)
    info: dict = {"title": "", "webpage_url": url, "channel": ""}
    blocked = None
    try:
        info = fetch.probe(url)
    except fetch.FetchError as exc:
        blocked = exc   # YouTube refuse souvent les serveurs : Gemini peut quand même lire le lien
    if _is_youtube(url) and "gemini" in ai.providers(cfg):
        md, name = ai.learn(cfg, question=question, youtube_url=info.get("webpage_url") or url)
        if md:
            return md, name + " (lien YouTube lu directement)", info
    if blocked:
        raise blocked
    src = Path(url) if fetch.is_local(url) else fetch.download_full(url, work / "src", "watch", 480)
    dur = probe_media(src)["duration"]
    words, _ = transcribe.transcribe(src, 0.0, min(dur, 1200.0),
                                     model_name=cfg["captions"]["whisper_model"], workdir=work)
    transcript = transcribe.words_to_lines(words, 0.0, per_line=14, limit=12000)
    proxy = make_proxy(src, 0.0, min(dur, 1200.0), work / "proxy.mp4") \
        if "gemini" in ai.providers(cfg) else None
    sh = contact_sheet(src, 0.0, dur, work / "sheet.jpg", cols=4, rows=3)
    md, name = ai.learn(cfg, question=question, video=proxy,
                        sheet=sh.read_bytes() if sh else None, transcript=transcript)
    if not md:
        md = ("## Transcription brute\n\nAucune IA disponible pour rédiger la fiche. "
              "Voici ce qui est dit dans la vidéo :\n\n" + (transcript or "(aucune parole détectée)"))
        name = "transcription seule"
    return md, name, info


def run_learn(text: str, cfg: dict, base: Path) -> int:
    urls = [u.rstrip(".,;:!?") for u in URL_RE.findall(text or "")]
    question = " ".join(URL_RE.sub(" ", text or "").split())
    question = re.sub(r"^(%s)\b[\s:,]*" % "|".join(KEYWORDS), "", question, flags=re.I).strip()
    if not urls:
        msg = "Aucun lien trouvé. Exemple : `apprends https://youtu.be/XXXX comment il monte ses clips`"
        deliver.issue_comment(msg)
        deliver.step_summary(msg)
        return 1
    KNOWLEDGE.mkdir(exist_ok=True)
    done = 0
    for i, url in enumerate(urls[:3]):
        try:
            md, name, info = study(url, question, cfg, base / f"learn{i}")
        except fetch.FetchError as exc:
            msg = f"### ❌ {url}\n{exc}\n\n{exc.hint}"
            deliver.issue_comment(msg)
            deliver.step_summary(msg)
            log(f"Échec : {exc} {exc.hint}")
            continue
        title = info.get("title") or url.rstrip("/").split("/")[-1] or "video"
        path = KNOWLEDGE / f"{datetime.now(timezone.utc):%Y%m%d}-{slugify(title, 50)}.md"
        header = (f"# Fiche : {title}\n\n- Source : {info.get('webpage_url') or url}\n"
                  f"- Chaîne : {info.get('channel') or 'inconnue'}\n"
                  f"- Analyse : {name}, le {datetime.now(timezone.utc):%d/%m/%Y}\n"
                  + (f"- Question posée : {question}\n" if question else "") + "\n")
        path.write_text(header + md.strip() + "\n", encoding="utf-8")
        body = f"### 📘 Fiche enregistrée : `knowledge/{path.name}`\n\n" + header + md.strip()
        deliver.issue_comment(body[:60000])
        deliver.step_summary(body[:60000])
        done += 1
    return 0 if done else 1
