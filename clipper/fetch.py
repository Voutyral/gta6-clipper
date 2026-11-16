"""Récupération des sources avec yt-dlp (Twitch, Kick, YouTube, Google Drive, liens directs)."""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .util import CmdError, log, probe_media, run

_COOKIES: dict = {}


class FetchError(RuntimeError):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def is_local(url: str) -> bool:
    return not url.lower().startswith(("http://", "https://")) and Path(url).exists()


def _cookies_file() -> str:
    """Cookies optionnels (secret YTDLP_COOKIES_B64, format Netscape encodé en base64)."""
    if "path" not in _COOKIES:
        raw = os.environ.get("YTDLP_COOKIES_B64", "").strip()
        path = ""
        if raw:
            try:
                fd, path = tempfile.mkstemp(suffix=".txt")
                with os.fdopen(fd, "wb") as f:
                    f.write(base64.b64decode(raw))
            except Exception:
                path = ""
        _COOKIES["path"] = path
    return _COOKIES["path"]


def _base(playlist: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-progress", "--retries", "3",
           "--fragment-retries", "5", "--socket-timeout", "30", "--no-cache-dir"]
    cmd.append("--yes-playlist" if playlist else "--no-playlist")
    # YouTube exige un moteur JavaScript : deno par défaut, node en secours.
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    proxy = os.environ.get("YTDLP_PROXY", "").strip()
    if proxy:
        cmd += ["--proxy", proxy]
    cookies = _cookies_file()
    if cookies:
        cmd += ["--cookies", cookies]
    return cmd


def _hint(stderr: str) -> str:
    s = (stderr or "").lower()
    if "sign in to confirm" in s or "not a bot" in s:
        return ("YouTube bloque les téléchargements depuis les serveurs GitHub. Trois issues : "
                "un lien Twitch ou Kick, un lien Google Drive public vers le fichier, "
                "ou un proxy résidentiel dans le secret YTDLP_PROXY.")
    if "private" in s or "members-only" in s or "subscriber" in s or "login required" in s:
        return "La vidéo est privée ou réservée aux abonnés."
    if "unsupported url" in s:
        return "Ce site n'est pas pris en charge. Essaie un lien direct vers le fichier ou Google Drive."
    if "429" in s or "too many requests" in s:
        return "Le site limite les requêtes. Relance dans quelques minutes."
    if "geo" in s and "restrict" in s:
        return "La vidéo est bloquée dans le pays des serveurs GitHub (États-Unis)."
    if "unavailable" in s or "404" in s or "does not exist" in s:
        return "La vidéo est introuvable ou a été supprimée."
    if "requested format is not available" in s:
        return "Aucun format téléchargeable trouvé pour cette vidéo."
    return ""


def probe(url: str) -> dict:
    """Titre, chaîne et durée de la source, sans rien télécharger."""
    if is_local(url):
        info = probe_media(url)
        return {"duration": info["duration"], "title": Path(url).stem, "channel": "",
                "channel_id": "", "handle": "", "webpage_url": url, "extractor": "local",
                "is_live": False, "categories": [], "view_count": None}
    try:
        p = run(_base() + ["-J", "--", url], timeout=240)
    except CmdError as exc:
        raise FetchError("Impossible de lire la source.", _hint(exc.stderr)) from exc
    data = json.loads(p.stdout or "{}")
    if data.get("_type") == "playlist" and data.get("entries"):
        data = data["entries"][0] or {}
    channel = data.get("channel") or data.get("uploader") or data.get("creator") or ""
    uid = str(data.get("uploader_id") or "")
    # Pseudo à créditer : le @handle YouTube s'il existe, sinon le nom de la chaîne sans espaces.
    handle = uid if uid.startswith("@") else ("@" + "".join(str(channel).split()) if channel else "")
    return {
        "duration": data.get("duration"),
        "title": data.get("title") or "",
        "channel": channel,
        "handle": handle,
        "channel_id": data.get("channel_id") or data.get("uploader_id") or "",
        "webpage_url": data.get("webpage_url") or url,
        "extractor": str(data.get("extractor_key") or "").lower(),
        "is_live": bool(data.get("is_live")),
        "categories": data.get("categories") or [],
        "view_count": data.get("view_count"),
    }


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}"


def _download(url: str, out_dir: Path, name: str, extra: list[str], timeout: float) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        run(_base() + extra + ["-o", str(out_dir / f"{name}.%(ext)s"), "--", url], timeout=timeout)
    except CmdError as exc:
        raise FetchError("Téléchargement impossible.", _hint(exc.stderr)) from exc
    files = sorted((p for p in out_dir.glob(f"{name}.*")
                    if p.suffix not in (".part", ".ytdl", ".json", ".txt")),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise FetchError("Le téléchargement n'a produit aucun fichier.")
    return files[0]


def _format(max_h: int) -> list[str]:
    return ["-f", f"bv*[height<={max_h}]+ba/b[height<={max_h}]/b",
            "-S", f"res:{max_h},fps:60,vcodec:h264", "--merge-output-format", "mkv"]


def download_full(url: str, out_dir: Path, name: str, max_h: int = 1080) -> Path:
    return _download(url, out_dir, name, _format(max_h), 3600)


def download_section(url: str, start: float, end: float, out_dir: Path, name: str,
                     max_h: int = 1080) -> Path:
    """Télécharge seulement [start, end] : indispensable sur un stream de plusieurs heures."""
    log(f"Section {_ts(start)} → {_ts(end)}")
    return _download(url, out_dir, name, _format(max_h) + [
        "--download-sections", f"*{_ts(start)}-{_ts(end)}", "--force-keyframes-at-cuts"], 1800)


def download_audio(url: str, out_dir: Path, name: str = "scan",
                   max_seconds: float | None = None) -> Path:
    """Piste son seule (légère) pour repérer les moments forts d'une longue vidéo."""
    extra = ["-f", "wa/ba/w"]
    if max_seconds:
        extra += ["--download-sections", f"*0-{int(max_seconds)}"]
    return _download(url, out_dir, name, extra, 5400)


def list_entries(url: str, limit: int = 30) -> list[dict]:
    """Entrées d'une liste (clips d'une chaîne Twitch par exemple), sans téléchargement."""
    try:
        p = run(_base(playlist=True) + ["--flat-playlist", "-J", "--playlist-end", str(limit),
                                        "--", url], timeout=240)
    except CmdError as exc:
        raise FetchError("Impossible de lister les clips.", _hint(exc.stderr)) from exc
    data = json.loads(p.stdout or "{}")
    return [e for e in (data.get("entries") or []) if isinstance(e, dict)]
