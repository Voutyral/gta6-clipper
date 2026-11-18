"""Découverte de clips : les plus regardés du moment sur Twitch et Kick, sans clé ni compte.

Twitch : l'interface GraphQL publique du site (identifiant client du lecteur web, celui
qu'utilise aussi yt-dlp). Kick : l'API JSON du site, lue avec l'empreinte d'un navigateur
(curl_cffi) parce qu'elle est derrière Cloudflare.
Vérifié depuis les serveurs GitHub le 18 novembre 2026.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .util import log

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_TW_NODE = ("slug title viewCount durationSeconds createdAt url language thumbnailURL "
            "game { name } broadcaster { login displayName }")
_TW_PERIOD = {"24hr": "LAST_DAY", "day": "LAST_DAY", "7d": "LAST_WEEK", "week": "LAST_WEEK",
              "30d": "LAST_MONTH", "month": "LAST_MONTH"}
_KICK_TIME = {"24hr": "day", "day": "day", "7d": "week", "week": "week", "30d": "month", "month": "month"}


@dataclass
class Found:
    """Un clip trouvé, avant téléchargement."""
    platform: str
    id: str
    url: str
    title: str
    views: int
    duration: float
    created_at: datetime
    streamer: str            # identifiant (login Twitch, slug Kick)
    streamer_name: str
    language: str = ""       # code en minuscules (« fr »), vide si inconnu
    game: str = ""
    thumbnail: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.id}"

    def age_hours(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.created_at).total_seconds() / 3600.0)


def _when(text: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------- Twitch

def _gql(query: str) -> dict:
    r = requests.post(TWITCH_GQL, timeout=30, json={"query": query},
                      headers={"Client-Id": TWITCH_CLIENT_ID, "Content-Type": "application/json"})
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"])[:300])
    return data.get("data") or {}


def _criteria(period: str, languages: list[str] | None) -> str:
    crit = f"filter: {_TW_PERIOD.get(period, 'LAST_DAY')}"
    langs = [str(x).upper() for x in (languages or []) if str(x).strip()]
    if langs:
        crit += ", languages: [" + ", ".join(langs) + "]"
    return "{" + crit + "}"


def _twitch_node(node: dict, game_hint: str = "") -> Found | None:
    if not node or not node.get("slug"):
        return None
    b = node.get("broadcaster") or {}
    login = str(b.get("login") or "")
    return Found(
        platform="twitch", id=str(node["slug"]),
        url=node.get("url") or f"https://www.twitch.tv/{login}/clip/{node['slug']}",
        title=str(node.get("title") or ""), views=int(node.get("viewCount") or 0),
        duration=float(node.get("durationSeconds") or 0), created_at=_when(node.get("createdAt")),
        streamer=login, streamer_name=str(b.get("displayName") or login),
        language=str(node.get("language") or "").lower(),
        game=str((node.get("game") or {}).get("name") or game_hint),
        thumbnail=str(node.get("thumbnailURL") or ""))


def _edges(obj: dict | None) -> list[dict]:
    return [e.get("node") or {} for e in ((obj or {}).get("clips") or {}).get("edges") or []]


def twitch_game_clips(game: str, *, period: str = "24hr", languages: list[str] | None = None,
                      first: int = 30) -> list[Found]:
    q = (f"query {{ game(name: {json.dumps(game)}) {{ name clips(first: {int(first)}, "
         f"criteria: {_criteria(period, languages)}) {{ edges {{ node {{ {_TW_NODE} }} }} }} }} }}")
    data = _gql(q)
    out = [_twitch_node(n, game) for n in _edges(data.get("game"))]
    return [f for f in out if f]


def twitch_user_clips(login: str, *, period: str = "24hr", first: int = 20) -> list[Found]:
    q = (f"query {{ user(login: {json.dumps(login.lower())}) {{ clips(first: {int(first)}, "
         f"criteria: {_criteria(period, None)}) {{ edges {{ node {{ {_TW_NODE} }} }} }} }} }}")
    data = _gql(q)
    out = [_twitch_node(n) for n in _edges(data.get("user"))]
    return [f for f in out if f]


# ---------------------------------------------------------------- Kick

def _kick_get(url: str) -> dict:
    try:
        from curl_cffi import requests as creq
        r = creq.get(url, impersonate="chrome", timeout=30)
    except ImportError:   # sans curl_cffi, Cloudflare refuse presque toujours
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    if r.status_code == 404:
        return {}
    if r.status_code != 200 or "json" not in str(r.headers.get("content-type", "")):
        raise RuntimeError(f"HTTP {r.status_code} {str(r.text)[:160]}")
    return r.json()


def _kick_clip(c: dict, game_hint: str = "") -> Found | None:
    if not c or not c.get("id"):
        return None
    ch = c.get("channel") or {}
    slug = str(ch.get("slug") or ch.get("username") or "")
    cat = c.get("category") or {}
    return Found(
        platform="kick", id=str(c["id"]),
        url=f"https://kick.com/{slug}/clips/{c['id']}" if slug else str(c.get("clip_url") or ""),
        title=str(c.get("title") or ""), views=int(c.get("views") or c.get("view_count") or 0),
        duration=float(c.get("duration") or 0), created_at=_when(c.get("created_at")),
        streamer=slug, streamer_name=str(ch.get("username") or slug), language="",
        game=str(cat.get("name") or game_hint), thumbnail=str(c.get("thumbnail_url") or ""),
        extra={"m3u8": c.get("clip_url") or ""})


def kick_category_clips(slug: str, *, period: str = "24hr") -> list[Found]:
    t = _KICK_TIME.get(period, "day")
    data = _kick_get(f"https://kick.com/api/v2/categories/{slug}/clips?cursor=0&sort=view&time={t}")
    out = [_kick_clip(c, slug) for c in (data.get("clips") or [])]
    return [f for f in out if f]


def kick_channel_clips(slug: str, *, period: str = "24hr") -> list[Found]:
    t = _KICK_TIME.get(period, "day")
    data = _kick_get(f"https://kick.com/api/v2/channels/{slug}/clips?cursor=0&sort=view&time={t}")
    out = [_kick_clip(c) for c in (data.get("clips") or [])]
    return [f for f in out if f]


# ---------------------------------------------------------------- Tout ensemble

def discover(cfg: dict, streamers: list[dict]) -> tuple[list[Found], list[str]]:
    """Toutes les sources activées. Retourne (clips trouvés, messages d'erreur lisibles)."""
    hc = cfg["hunt"]
    period = str(hc.get("period", "24hr"))
    found: list[Found] = []
    errors: list[str] = []

    def collect(label: str, fn, *args, **kw) -> None:
        try:
            got = fn(*args, **kw)
            log(f"Chasse : {label} → {len(got)} clip(s)")
            found.extend(got)
        except Exception as exc:
            errors.append(f"{label} : {exc}")
            log(f"Chasse : {label} illisible ({exc})")

    tw = hc.get("twitch") or {}
    if tw.get("enabled", True):
        for game in tw.get("games") or []:
            collect(f"Twitch « {game} »", twitch_game_clips, game, period=period,
                    languages=tw.get("languages") or [], first=int(tw.get("per_game", 30)))
    kk = hc.get("kick") or {}
    if kk.get("enabled", True):
        for slug in kk.get("categories") or []:
            collect(f"Kick « {slug} »", kick_category_clips, slug, period=period)
    for s in streamers:   # streamers autorisés : suivis même hors des catégories ci-dessus
        if s.get("twitch"):
            collect(f"Twitch @{s['twitch']}", twitch_user_clips, str(s["twitch"]), period=period)
        if s.get("kick"):
            collect(f"Kick @{s['kick']}", kick_channel_clips, str(s["kick"]), period=period)
    unique: dict[str, Found] = {}
    for f in found:
        unique.setdefault(f.key, f)
    return list(unique.values()), errors
