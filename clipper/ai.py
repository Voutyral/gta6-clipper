"""Accès aux modèles d'IA, du plus capable au plus simple.

1. Gemini (secret GEMINI_API_KEY, clé gratuite Google AI Studio) : regarde la vidéo elle-même,
   image et son, et lit un lien YouTube public sans téléchargement.
2. Claude (secret ANTHROPIC_API_KEY) : lit une planche d'images et la transcription.
3. GitHub Models : gratuit, sans clé, fourni par le jeton du workflow (permission models: read).
Sans aucun des trois, le pipeline tourne quand même avec des règles simples.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import requests

from .util import log

GEMINI = "https://generativelanguage.googleapis.com"
_state: dict = {"gemini_model": None, "github_model": None, "github_catalog": None,
                "off": set(), "fails": {}}


def providers(cfg: dict) -> list[str]:
    if not cfg["ai"].get("enabled", True):
        return []
    out = []
    if os.environ.get("GEMINI_API_KEY"):
        out.append("gemini")
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append("claude")
    if os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_ACTIONS"):
        out.append("github")
    return [p for p in out if p not in _state["off"]]


def parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    for candidate in (t, t[t.find("{"): t.rfind("}") + 1] if "{" in t and "}" in t else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                data = data[0]
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- Gemini

def gemini_upload(path: Path, mime: str = "video/mp4") -> str | None:
    """Envoie un fichier à Gemini (Files API) et attend qu'il soit prêt."""
    key = os.environ["GEMINI_API_KEY"]
    size = path.stat().st_size
    try:
        r = requests.post(f"{GEMINI}/upload/v1beta/files", timeout=60, headers={
            "x-goog-api-key": key, "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start", "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime, "Content-Type": "application/json",
        }, json={"file": {"display_name": path.name}})
        url = r.headers.get("x-goog-upload-url")
        if not url:
            log(f"Gemini : envoi refusé (HTTP {r.status_code} {r.text[:160]})")
            return None
        with open(path, "rb") as f:
            r2 = requests.post(url, timeout=600, data=f, headers={
                "Content-Length": str(size), "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize"})
        info = (r2.json() or {}).get("file", {})
        name, uri, state = info.get("name"), info.get("uri"), info.get("state")
        for _ in range(60):
            if state == "ACTIVE":
                return uri
            if state == "FAILED" or not name:
                return None
            time.sleep(3)
            state = requests.get(f"{GEMINI}/v1beta/{name}", headers={"x-goog-api-key": key},
                                 timeout=30).json().get("state")
    except Exception as exc:
        log(f"Gemini : envoi du fichier impossible ({exc})")
    return None


def _gemini(cfg: dict, parts: list[dict], want_json: bool) -> str | None:
    key = os.environ["GEMINI_API_KEY"]
    models = [_state["gemini_model"]] if _state["gemini_model"] else list(cfg["ai"]["gemini_models"])
    body: dict = {"contents": [{"role": "user", "parts": parts}]}
    if want_json:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    for model in models:
        for attempt in range(2):
            try:
                r = requests.post(f"{GEMINI}/v1beta/models/{model}:generateContent", timeout=600,
                                  headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                  json=body)
            except requests.RequestException as exc:
                log(f"Gemini injoignable : {exc}")
                break
            if r.status_code == 200:
                _state["gemini_model"] = model
                try:
                    cand = r.json()["candidates"][0]
                    # Les parties « thought » sont le raisonnement interne du modèle, pas la réponse.
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"]
                                   if not p.get("thought"))
                except Exception:
                    text = ""
                if text.strip():
                    return text
                log(f"Gemini {model} : réponse vide ou bloquée ({r.text[:300]})")
                break
            if r.status_code in (429, 500, 503) and attempt == 0:
                time.sleep(20 if r.status_code == 429 else 8)
                continue
            log(f"Gemini {model} : HTTP {r.status_code} {r.text[:200]}")
            break
    return None


# ---------------------------------------------------------------- Claude et GitHub Models

def _claude(cfg: dict, system: str, text: str, sheet: bytes | None) -> str | None:
    content: list[dict] = []
    if sheet:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.b64encode(sheet).decode()}})
    content.append({"type": "text", "text": text})
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=180, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, json={"model": cfg["ai"]["claude_model"], "max_tokens": 2000, "system": system,
                 "messages": [{"role": "user", "content": content}]})
    except requests.RequestException as exc:
        log(f"Claude injoignable : {exc}")
        return None
    if r.status_code != 200:
        log(f"Claude : HTTP {r.status_code} {r.text[:200]}")
        return None
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def _github_headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json", "Content-Type": "application/json"}


def _github_models(cfg: dict) -> list[str]:
    """Modèles de config.yaml encore présents au catalogue ; sinon les premiers modèles OpenAI trouvés."""
    wanted = list(cfg["ai"]["github_models"])
    if _state.get("github_catalog") is None:
        ids: list[str] = []
        try:
            r = requests.get("https://models.github.ai/catalog/models", headers=_github_headers(), timeout=30)
            if r.status_code == 200 and _is_stub(r):
                pass   # service fermé pour ce dépôt : l'appel suivant le signalera une fois
            elif r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else data.get("models", [])
                ids = [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")]
            else:
                log(f"GitHub Models : catalogue HTTP {r.status_code} {r.text[:120]}")
        except Exception as exc:
            log(f"GitHub Models : catalogue illisible ({exc})")
        _state["github_catalog"] = ids
    ids = _state["github_catalog"]
    if not ids:
        return wanted
    present = [m for m in wanted if m in ids]
    if present:
        return present
    found = [i for i in ids if i.startswith("openai/gpt") and "embed" not in i][:4]
    log(f"GitHub Models : aucun modèle de config.yaml au catalogue. Essai avec {found}. "
        f"Disponibles : {', '.join(ids[:40])}")
    return found or wanted


def _is_stub(r) -> bool:
    """Le service répond parfois un simple « OK » en texte brut : il n'est pas ouvert pour ce dépôt."""
    return str(getattr(r, "headers", {}).get("content-type", "")).startswith("text/plain") \
        and (r.text or "").strip() == "OK"


def _github_once(model: str, system: str, text: str, sheet: bytes | None) -> tuple[str | None, int]:
    content: list[dict] = [{"type": "text", "text": text}]
    if sheet:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(sheet).decode()}})
    r = requests.post("https://models.github.ai/inference/chat/completions", timeout=180,
                      headers=_github_headers(),
                      json={"model": model, "messages": [
                          {"role": "system", "content": system},
                          {"role": "user", "content": content if sheet else text}]})
    if r.status_code != 200:
        log(f"GitHub Models {model} : HTTP {r.status_code} {r.text[:200]}")
        return None, r.status_code
    if _is_stub(r):
        return None, -1
    try:
        out = r.json()["choices"][0]["message"].get("content")
        if isinstance(out, list):   # certains modèles répondent par morceaux
            out = "".join(p.get("text", "") for p in out if isinstance(p, dict))
    except Exception:
        out = None
    if not out:
        log(f"GitHub Models {model} : réponse vide ({r.text[:300]})")
    return (out or None), 200


def _github(cfg: dict, system: str, text: str, sheet: bytes | None) -> str | None:
    models = [_state["github_model"]] if _state["github_model"] else _github_models(cfg)
    # Avec l'image d'abord ; si aucun modèle ne répond, un second tour en texte seul.
    for with_sheet in ((True, False) if sheet else (False,)):
        for model in models:
            try:
                out, code = _github_once(model, system, text, sheet if with_sheet else None)
            except requests.RequestException as exc:
                log(f"GitHub Models injoignable : {exc}")
                return None
            if out:
                _state["github_model"] = model
                return out
            if code == -1:
                log("IA gratuite de GitHub : le service n'est pas ouvert pour ce dépôt, on s'en passe.")
                _state["off"].add("github")
                return None
            if code in (401, 403):
                return None
    return None


# ---------------------------------------------------------------- Interface commune

def ask(cfg: dict, system: str, text: str, *, video: Path | None = None,
        youtube_url: str | None = None, sheet: bytes | None = None,
        want_json: bool = True) -> tuple[str | None, str]:
    """Pose la question au meilleur modèle disponible. Retourne (réponse, fournisseur)."""
    for name in providers(cfg):
        answer = None
        if name == "gemini":
            parts: list[dict] = []
            if youtube_url:
                parts.append({"file_data": {"file_uri": youtube_url}})
            elif video and video.exists():
                uri = gemini_upload(video)
                if uri:
                    parts.append({"file_data": {"mime_type": "video/mp4", "file_uri": uri}})
            if not parts and sheet:
                parts.append({"inline_data": {"mime_type": "image/jpeg",
                                              "data": base64.b64encode(sheet).decode()}})
            parts.append({"text": system + "\n\n" + text})
            answer = _gemini(cfg, parts, want_json)
        elif name == "claude":
            answer = _claude(cfg, system, text, sheet)
        elif name == "github":
            answer = _github(cfg, system, text, sheet)
        if answer:
            _state["fails"][name] = 0
            return answer, name
        # Un refus isolé arrive (filtre de contenu, limite passagère) : on n'écarte
        # un fournisseur pour le reste du lancement qu'après deux échecs de suite.
        _state["fails"][name] = _state["fails"].get(name, 0) + 1
        if _state["fails"][name] >= 2:
            _state["off"].add(name)
        log(f"IA « {name} » sans réponse, on passe à la suivante.")
    return None, ""


EVAL_SYSTEM = """Tu es monteur de Shorts gaming ({game}). On te donne UN extrait candidat tiré d'un stream ou d'une vidéo : la vidéo ou une planche d'images, plus la transcription horodatée (secondes depuis le début de l'extrait). Évalue son potentiel en Short vertical et prépare son habillage.

Réponds uniquement par un objet JSON avec ces clés :
- "score" : entier de 0 à 10 (10 = moment très fort, compréhensible sans contexte)
- "what" : une phrase factuelle sur ce qui se passe
- "start" et "end" : début et fin conseillés, en secondes dans l'extrait. Garde la mise en place et la chute, coupe sur des fins de phrase, durée entre {min_s} et {max_s} secondes
- "hook" : accroche affichée à l'écran, 5 mots maximum, sans emoji
- "context" : une ligne de contexte de 8 mots maximum, ou "" si elle n'apporte rien
- "title" : titre de 70 caractères maximum
- "description" : une ou deux phrases naturelles, sans hashtag
- "tags" : 6 à 10 mots-clés sans #
- "risks" : liste, vide ou parmi "cinematique" (cinématique du jeu montrée seule), "spoiler" (fin du jeu ou révélation majeure de l'histoire), "violence_debut" (violence graphique dans les 7 premières secondes), "musique" (musique sous licence très présente, par exemple la radio du jeu)

Règles : écris en {lang}. N'invente aucun fait absent de l'extrait. Pas de titre mensonger. Aucun spoiler dans le titre ni dans l'accroche."""


def evaluate(cfg: dict, *, text: str, dur: float, peak: float | None, title: str, credit: str,
             lang: str, video: Path | None = None, sheet: bytes | None = None) -> tuple[dict | None, str]:
    det = cfg["detection"]
    system = EVAL_SYSTEM.format(game=cfg["metadata"]["game"], min_s=det["clip_min_s"],
                                max_s=det["clip_max_s"], lang=lang)
    user = (f"Source : « {title or 'inconnue'} ». Chaîne : {credit or 'inconnue'}.\n"
            f"Durée de l'extrait : {dur:.1f} s."
            + (f" Pic sonore à {peak:.1f} s." if peak is not None else "")
            + "\nTranscription :\n" + (text or "(aucune parole détectée)"))
    answer, name = ask(cfg, system, user, video=video, sheet=sheet)
    data = parse_json(answer)
    if answer and data is None:
        log(f"IA « {name} » : réponse illisible, ignorée : {answer[:400]!r}")
    if data is not None:
        log(f"IA « {name} » : score {data.get('score')}, accroche « {data.get('hook')} », "
            f"coupe {data.get('start')} → {data.get('end')} s, risques {data.get('risks')}")
    return data, (name if data is not None else "")


LEARN_SYSTEM = """Tu regardes une vidéo pour en tirer une méthode réutilisable (regarder, retenir, refaire). Écris une fiche en français, en Markdown, avec ces parties :
## En une phrase
## Ce que dit la vidéo (résumé fidèle, avec repères de temps mm:ss)
## La méthode pas à pas (étapes numérotées, outils cités, réglages précis)
## Le style de montage observé (cadrage, sous-titres, rythme des plans, sons, textes à l'écran)
## Ce qu'on peut réutiliser pour des Shorts gaming (liste concrète)
## Limites et points à vérifier
N'invente rien : si une information n'est pas dans la vidéo, écris « non précisé »."""


def learn(cfg: dict, *, question: str, youtube_url: str | None = None, video: Path | None = None,
          sheet: bytes | None = None, transcript: str = "") -> tuple[str | None, str]:
    user = (question or "Tire de cette vidéo la méthode complète.")
    if transcript:
        user += "\n\nTranscription :\n" + transcript[:12000]
    return ask(cfg, LEARN_SYSTEM, user, youtube_url=youtube_url, video=video, sheet=sheet,
               want_json=False)
