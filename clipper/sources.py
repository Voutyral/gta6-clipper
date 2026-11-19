"""Découverte de clips : les plus regardés du moment sur Twitch et Kick, sans clé ni compte.

Twitch : l'interface GraphQL publique du site (identifiant client du lecteur web, celui
qu'utilise aussi yt-dlp). Kick : l'API JSON du site, lue avec l'empreinte d'un navigateur
(curl_cffi) parce qu'elle est derrière Cloudflare.
Vérifié depuis les serveurs GitHub le 18 novembre 2026.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from . import campaign
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
    """Date d'une API ; sans fuseau (« 2026-11-19 14:00:00 » chez Kick), c'est de l'UTC."""
    try:
        d = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


# ---------------------------------------------------------------- Twitch

def _gql(query: str, tries: int = 3) -> dict:
    """Requête GraphQL Twitch. « service error » est passager (une recherche sur deux le 18/11) : on réessaie."""
    for attempt in range(tries):
        r = requests.post(TWITCH_GQL, timeout=30, json={"query": query},
                          headers={"Client-Id": TWITCH_CLIENT_ID, "Content-Type": "application/json"})
        if r.status_code >= 500 and attempt + 1 < tries:
            time.sleep(2 + 3 * attempt)
            continue
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            if attempt + 1 < tries and "service error" in str(data["errors"]).lower():
                time.sleep(2 + 3 * attempt)
                continue
            raise RuntimeError(str(data["errors"])[:300])
        return data.get("data") or {}
    return {}


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
        extra={"m3u8": c.get("clip_url") or c.get("video_url") or "",
               # numéro du live d'où vient le clip : sert à reconnaître le stream d'une campagne
               "livestream_id": str(c.get("livestream_id") or ""),
               "mature": bool(c.get("is_mature"))})


def kick_clip(clip_id: str) -> Found | None:
    """Un clip Kick précis, retrouvé par son identifiant (pour le refaire après une correction)."""
    try:
        data = _kick_get(f"https://kick.com/api/v2/clips/{clip_id}")
    except Exception as exc:
        log(f"Kick : clip {clip_id} illisible ({exc}).")
        return None
    return _kick_clip(data.get("clip") or data)


def kick_category_clips(slug: str, *, period: str = "24hr") -> list[Found]:
    t = _KICK_TIME.get(period, "day")
    data = _kick_get(f"https://kick.com/api/v2/categories/{slug}/clips?cursor=0&sort=view&time={t}")
    out = [_kick_clip(c, slug) for c in (data.get("clips") or [])]
    return [f for f in out if f]


def kick_channel_clips(slug: str, *, period: str = "24hr", sort: str = "view", pages: int = 1,
                       since: datetime | None = None) -> list[Found]:
    """Clips d'une chaîne Kick. Avec `sort="date"`, les plus récents d'abord : on tourne les pages
    jusqu'à `since` (début de la fenêtre d'une campagne) ou jusqu'à `pages` pages."""
    t = _KICK_TIME.get(period, "day")
    out: list[Found] = []
    cursor = "0"
    for _ in range(max(1, int(pages))):
        data = _kick_get(f"https://kick.com/api/v2/channels/{slug}/clips?cursor={cursor}&sort={sort}&time={t}")
        got = [f for f in (_kick_clip(c) for c in (data.get("clips") or [])) if f]
        out.extend(got)
        cursor = str(data.get("nextCursor") or data.get("next_cursor") or "")
        if not got or not cursor or cursor == "0":
            break
        if since is not None and min(f.created_at for f in got) < since:
            break
    return out


def kick_channel_videos(slug: str) -> list[Found]:
    """Rediffusions (VOD) d'une chaîne Kick : l'outil y repère lui-même les moments forts."""
    data = _kick_get(f"https://kick.com/api/v2/channels/{slug}/videos")
    items = data if isinstance(data, list) else (data.get("data") or data.get("videos") or [])
    out = []
    for v in items:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        video = v.get("video") or {}
        uuid = str(video.get("uuid") or "")
        cats = v.get("categories") or []
        game = str((cats[0] or {}).get("name") or "") if cats else ""
        out.append(Found(
            platform="kick", id=f"vod-{uuid or v['id']}",
            url=f"https://kick.com/video/{uuid}" if uuid else str(v.get("source") or ""),
            title=str(v.get("session_title") or ""), views=int(v.get("views") or video.get("views") or 0),
            duration=float(v.get("duration") or 0) / 1000.0,   # millisecondes chez Kick
            created_at=_when(v.get("start_time") or v.get("created_at")), streamer=slug, streamer_name=slug,
            game=game, extra={"vod": True, "m3u8": str(v.get("source") or ""),
                              "livestream_id": str(v["id"]), "live": bool(v.get("is_live"))}))
    return out


GTA6_WORDS = ("gta 6", "gta vi", "gta6", "gtavi", "grand theft auto vi", "grand theft auto 6")


def youtube_channel_videos(handle: str, limit: int = 8) -> list[Found]:
    """Lives terminés et vidéos récentes d'une chaîne YouTube (lus sans téléchargement)."""
    from . import fetch  # import local : la veille des lives n'en a pas besoin
    handle = "@" + str(handle).lstrip("@")
    out = []
    for tab in ("streams", "videos"):
        for e in fetch.list_entries(f"https://www.youtube.com/{handle}/{tab}", limit=limit,
                                    extra=["--extractor-args", "youtubetab:approximate_date"]):
            vid = str(e.get("id") or "")
            if not vid:
                continue
            title = str(e.get("title") or "")
            ts = e.get("timestamp") or e.get("release_timestamp")
            when = datetime.fromtimestamp(float(ts), timezone.utc) if ts else datetime.now(timezone.utc)
            out.append(Found(
                platform="youtube", id=vid, url=f"https://www.youtube.com/watch?v={vid}", title=title,
                views=int(e.get("view_count") or 0), duration=float(e.get("duration") or 0), created_at=when,
                streamer=handle.lstrip("@").lower(), streamer_name=handle,
                game="Grand Theft Auto VI" if any(w in title.lower() for w in GTA6_WORDS) else "",
                extra={"vod": True, "live": str(e.get("live_status") or "") in ("is_live", "is_upcoming"),
                       "dated": bool(ts)}))
    return out


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
        # Une recherche par langue : sinon les clips anglais, bien plus vus, noient les français.
        langs = [str(x).lower() for x in tw.get("languages") or [] if str(x).strip()]
        for game in tw.get("games") or []:
            for group in ([[x] for x in langs] if langs else [[]]):
                label = f"Twitch « {game} »" + (f" {group[0].upper()}" if group else "")
                collect(label, twitch_game_clips, game, period=period, languages=group,
                        first=int(tw.get("per_game", 30)))
    kk = hc.get("kick") or {}
    if kk.get("enabled", True):
        for slug in kk.get("categories") or []:
            collect(f"Kick « {slug} »", kick_category_clips, slug, period=period)
    for s in streamers:   # streamers autorisés : suivis même hors des catégories ci-dessus
        camp = campaign.of(s)
        if camp and campaign.ended(camp):
            continue
        if s.get("twitch"):
            collect(f"Twitch @{s['twitch']}", twitch_user_clips, str(s["twitch"]),
                    period="30d" if camp else period)
        if s.get("kick"):
            if camp:
                # Campagne : tous les clips du stream autorisé, même plusieurs jours après,
                # les plus vus du mois et les plus récents (page après page), plus ses rediffusions
                # où l'outil fait ses propres clips.
                since = campaign.window(camp)[0]
                collect(f"Kick @{s['kick']} (plus vus)", kick_channel_clips, str(s["kick"]), period="30d")
                collect(f"Kick @{s['kick']} (récents)", kick_channel_clips, str(s["kick"]), period="30d",
                        sort="date", pages=6, since=since)
                collect(f"Kick @{s['kick']} (rediffusions)", kick_channel_videos, str(s["kick"]))
            else:
                collect(f"Kick @{s['kick']}", kick_channel_clips, str(s["kick"]), period=period)
        if s.get("youtube") and camp:
            collect(f"YouTube {s['youtube']} (lives et vidéos)", youtube_channel_videos, str(s["youtube"]))
    unique: dict[str, Found] = {}
    for f in found:
        unique.setdefault(f.key, f)
    return list(unique.values()), errors
