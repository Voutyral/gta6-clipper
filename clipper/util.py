from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import unicodedata
from pathlib import Path

_SECRET_KEYS = (
    "TELEGRAM_BOT_TOKEN", "YTDLP_PROXY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "YT_REFRESH_TOKEN", "YT_CLIENT_SECRET", "GITHUB_TOKEN", "GH_TOKEN",
)


def mask(text: str) -> str:
    """Retire les secrets d'un texte avant affichage."""
    for key in _SECRET_KEYS:
        val = os.environ.get(key, "")
        if val and len(val) >= 6:
            text = text.replace(val, "***")
    return text


def log(msg: object) -> None:
    print(mask(str(msg)), flush=True)


class CmdError(RuntimeError):
    def __init__(self, cmd: list[str], code: int, stderr: str):
        self.cmd, self.code, self.stderr = cmd, code, stderr or ""
        tail = "\n".join(self.stderr.strip().splitlines()[-12:])
        super().__init__(mask(f"commande échouée ({code}) : {' '.join(cmd[:4])}…\n{tail}"))


def run(cmd, *, timeout: float | None = None, check: bool = True, quiet: bool = False,
        cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """Lance une commande sans shell. Lève CmdError si elle échoue."""
    cmd = [str(c) for c in cmd]
    if not quiet:
        log("$ " + " ".join(shlex.quote(c) for c in cmd)[:500])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                       errors="replace")
    if check and p.returncode != 0:
        raise CmdError(cmd, p.returncode, p.stderr)
    return p


def probe_media(path: str | Path) -> dict:
    """Durée, taille d'image, cadence et présence de son d'un fichier."""
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
             str(path)], quiet=True)
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    def rate(key: str) -> float:
        try:
            num, den = str(v.get(key, "0/1")).split("/")
            return float(num) / float(den) if float(den) else 0.0
        except Exception:
            return 0.0

    fps = 30.0
    if v:
        fps = rate("avg_frame_rate") or rate("r_frame_rate") or 30.0
        if not 1.0 <= fps <= 240.0:
            fps = 30.0
    duration = float(fmt.get("duration") or (v or {}).get("duration") or 0.0)
    return {
        "duration": duration,
        "width": int(v["width"]) if v else 0,
        "height": int(v["height"]) if v else 0,
        "fps": fps,
        "has_audio": a is not None,
        "has_video": v is not None,
        "size": int(fmt.get("size") or 0),
    }


def slugify(text: str, limit: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:limit].strip("-") or "clip"


def fmt_ts(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
