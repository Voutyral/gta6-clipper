from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# Valeurs par défaut : config.yaml ne fait que les surcharger.
DEFAULTS: dict = {
    "metadata": {
        "game": "GTA 6",
        "language": "auto",                  # auto : textes dans la langue parlée du clip
        "languages": ["fr", "en"],           # langues possibles des textes (la première par défaut)
        "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
        "hashtags_by_game": {"Grand Theft Auto VI": ["#GTA6", "#GTAVI"],
                             "Grand Theft Auto V": ["#GTA5", "#GTAV", "#GTARP"]},
        "hashtags_common": ["#Shorts"],
        "fallback_hooks": ["ATTENDS LA FIN", "PERSONNE S'Y ATTENDAIT", "IL A VRAIMENT FAIT ÇA",
                           "GTA 6 EST FOU"],
        "fallback_hooks_by_lang": {"en": ["WAIT FOR IT", "NOBODY SAW THIS COMING", "HE REALLY DID THAT",
                                          "GTA 6 IS INSANE"]},
    },
    "detection": {
        "clips_per_video": 2,
        "extra_candidates": 2,
        "clip_min_s": 15,
        "clip_max_s": 45,
        "pre_peak_s": 12,
        "post_peak_s": 14,
        "keep_whole_if_under_s": 50,
    },
    "fetch": {"max_height": 1080, "full_download_max_s": 900, "max_scan_s": 14400},
    "edit": {
        "layout": "auto",
        "zoom": 1.5,
        "teaser": "auto",
        "teaser_s": 1.8,
        "punch": True,
        "punch_zoom": 1.12,
        "shake_px": 12,
        "max_punches": 3,
        "sfx": True,
        "sfx_gain_db": -9,
        "music": "auto",
        "music_gain_db": -21,
        "hook_seconds": 3.5,
        "auto_facecam": True,
        "jump_cuts": True,
        "jump_zoom": 1.07,
        "max_cuts": 10,
        "tighten": True,
        "tighten_gap_s": 0.75,
        "tighten_leave_s": 0.25,
        "tighten_quiet_db": 9.0,
        "tighten_motion": 5.0,
        "tighten_max": 8,
        "tighten_max_share": 0.25,
        "voice_boost": True,
        "lra": 7,
        "min_after_cuts_s": 10,
        "min_keep_ratio": 0.55,
        "credit_prefix": "Clip :",
        "x264_preset": "veryfast",
    },
    "captions": {
        "enabled": True,
        "engine": "parakeet",
        "ai_fix": True,
        "whisper_model": "small",
        "style": "reveal",
        "max_words": 3,
        "max_chars": 13,
        "highlight": "&H0000E5FF",
    },
    "ai": {
        "enabled": True,
        "gemini_models": ["gemini-3-flash-preview", "gemini-3.8-flash", "gemini-2.5-flash"],
        "gemini_timeout_s": 120,
        "github_models": ["openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4.1",
                          "openai/gpt-4o"],
        "claude_model": "claude-haiku-5-5",
        "gemini_thinking": "low",
    },
    "delivery": {"release": True, "keep_days": 7},
    "youtube": {"mode": "off", "category_id": "20"},
    "hunt": {
        "enabled": True,
        "period": "24hr",
        "max_per_run": 6,
        "max_per_day": 60,
        "campaign_max_per_run": 5,
        "max_candidates": 16,
        "mix_languages": True,
        "min_ai_score": 4,
        "game_boost": {"Grand Theft Auto VI": 3},
        "min_views": 150,
        "min_views_floor": 40,
        "max_age_hours": 30,
        "min_duration_s": 8,
        "max_duration_s": 120,
        "max_per_streamer_day": 2,
        "max_unknown_language": 1,
        "allowlist_only": False,
        "allowlist_boost": 2.0,
        "blocked_streamers": [],
        "exclude_words": ["spoiler", "ending", "fin du jeu", "final mission", "dernière mission",
                          "cinématique", "cinematic", "cutscene"],
        "require_languages": ["fr", "en"],
        "twitch": {"enabled": True, "games": ["Grand Theft Auto VI", "Grand Theft Auto V"],
                   "languages": ["fr", "en"], "per_game": 50},
        "kick": {"enabled": True, "categories": ["grand-theft-auto-vi", "grand-theft-auto-v"]},
    },
    "gallery": {"days": 7, "max_clips": 60, "max_mb": 900, "hide": [],
                "schedule_utc": [f"{h:02d}:17" for h in range(1, 24, 2)]},
}


def _merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, val in (over or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _merge(DEFAULTS, data or {})


def load_streamers(path: str | Path | None = None) -> list[dict]:
    """Liste des streamers autorisés (campagne rejointe ou accord)."""
    p = Path(path) if path else ROOT / "streamers.yaml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [s for s in (data.get("streamers") or []) if isinstance(s, dict) and s.get("name")]


def match_streamer(streamers: list[dict], *names: str) -> dict:
    """Retrouve la fiche d'un streamer à partir d'un nom de chaîne ou d'un @pseudo."""
    wanted = {str(n).lower().lstrip("@").strip() for n in names if n}
    wanted.discard("")
    for s in streamers:
        keys = {str(s.get(k, "")).lower().lstrip("@").strip()
                for k in ("name", "twitch", "kick", "youtube", "credit")}
        keys.discard("")
        if wanted & keys:
            return s
    return {}
