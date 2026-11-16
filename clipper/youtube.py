"""Envoi automatique sur YouTube (optionnel).

Actif seulement si les secrets YT_CLIENT_ID, YT_CLIENT_SECRET et YT_REFRESH_TOKEN existent
et que youtube.mode vaut « private » ou « public » dans config.yaml.
Le statut réellement appliqué par YouTube est relu dans la réponse et rapporté tel quel.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .util import log

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def enabled(cfg: dict) -> bool:
    mode = str(cfg["youtube"].get("mode", "off")).lower()
    return mode in ("private", "public", "unlisted") and all(
        os.environ.get(k, "").strip() for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def _access_token() -> tuple[str, str]:
    r = requests.post(TOKEN_URL, timeout=60, data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"})
    data = r.json() if r.content else {}
    if r.status_code != 200 or not data.get("access_token"):
        err = data.get("error", r.status_code)
        hint = ""
        if err == "invalid_grant":
            hint = (" Le jeton a expiré : tant que l'écran de consentement Google est en mode"
                    " « Test », il ne vit que 7 jours. Passe-le en production puis refais le jeton.")
        return "", f"connexion Google refusée ({err}).{hint}"
    return data["access_token"], ""


def upload(path: Path, *, title: str, description: str, tags: list[str], cfg: dict,
           lang: str = "fr", paid: bool = False) -> dict:
    """Retourne {"url", "privacy"} ou {"error"}."""
    token, err = _access_token()
    if not token:
        return {"error": err}
    mode = str(cfg["youtube"].get("mode", "private")).lower()
    meta: dict = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                    "categoryId": str(cfg["youtube"].get("category_id", "20")),
                    "defaultLanguage": lang or "fr"},
        "status": {"privacyStatus": mode, "selfDeclaredMadeForKids": False},
    }
    parts = "snippet,status"
    if paid:
        meta["paidProductPlacementDetails"] = {"hasPaidProductPlacement": True}
        parts += ",paidProductPlacementDetails"
    size = path.stat().st_size
    try:
        r = _start(token, parts, meta, size)
        if r.status_code == 400 and paid:
            # Champ « promotion payante » refusé : on envoie sans, et on le signale.
            meta.pop("paidProductPlacementDetails", None)
            r = _start(token, "snippet,status", meta, size)
            log("YouTube : champ promotion payante refusé, à cocher à la main.")
        location = r.headers.get("Location")
        if r.status_code != 200 or not location:
            return {"error": f"HTTP {r.status_code} {r.text[:200]}"}
        with open(path, "rb") as f:
            r2 = requests.put(location, data=f, timeout=1800,
                              headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
        data = r2.json() if r2.content else {}
        if r2.status_code not in (200, 201) or not data.get("id"):
            return {"error": f"HTTP {r2.status_code} {r2.text[:200]}"}
    except Exception as exc:
        return {"error": str(exc)}
    applied = (data.get("status") or {}).get("privacyStatus", "?")
    if applied != mode:
        log(f"YouTube a appliqué « {applied} » au lieu de « {mode} ».")
    return {"url": f"https://youtube.com/shorts/{data['id']}", "privacy": applied, "id": data["id"]}


def _start(token: str, parts: str, meta: dict, size: int) -> requests.Response:
    return requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": parts}, timeout=60,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4",
                                  "X-Upload-Content-Length": str(size)},
                         data=json.dumps(meta).encode("utf-8"))
