"""Lecture d'une demande écrite au téléphone, en texte libre.

Exemples acceptés :
    https://www.twitch.tv/videos/123 1:02:30-1:03:10 @streamer
    https://clips.twitch.tv/Abc "IL SAUTE DU TOIT" #crop
    https://youtu.be/xyz 12:40 x2 #noteaser
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

URL_RE = re.compile(r"https?://[^\s<>\"'\]\)]+", re.I)
_TIME = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?"
RANGE_RE = re.compile(rf"(?<![\d:])({_TIME})\s*(?:-|–|—|→|>|à|a|to)\s*({_TIME})(?![\d:])", re.I)
MOMENT_RE = re.compile(rf"(?<![\d:])({_TIME})(?![\d:])")
CREDIT_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_.]{2,30})")
HOOK_RE = re.compile(r"[\"“«]\s*([^\"”»]{3,80}?)\s*[\"”»]")
FLAG_RE = re.compile(r"(?<!\w)#(\w+)")
COUNT_RE = re.compile(r"(?<![\w.])x\s?([1-5])(?!\w)", re.I)
URL_T_RE = re.compile(r"[?&#]t=((?:\d+h)?(?:\d+m)?(?:\d+s?)?)(?:&|$)", re.I)

LANGS = {"fr", "en", "es", "de", "it", "pt"}
LAYOUTS = ("blur", "crop", "split", "fill")


@dataclass
class ClipRequest:
    url: str
    start: float | None = None
    end: float | None = None
    moment: float | None = None
    credit: str | None = None
    hook: str | None = None
    layout: str | None = None
    count: int | None = None
    lang: str | None = None
    flags: set[str] = field(default_factory=set)
    raw: str = ""


def to_seconds(text: str) -> float:
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def url_time(url: str) -> float | None:
    """Horodatage porté par le lien lui-même (?t=754, ?t=1h2m3s)."""
    m = URL_T_RE.search(url)
    if not m or not m.group(1):
        return None
    spec = m.group(1).lower()
    if spec.isdigit():
        return float(spec)
    parts = re.match(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$", spec)
    if not parts:
        return None
    h, mn, s = (int(x) if x else 0 for x in parts.groups())
    total = h * 3600 + mn * 60 + s
    return float(total) if total else None


def _parse_block(text: str) -> list[ClipRequest]:
    urls = [u.rstrip(".,;:!?") for u in URL_RE.findall(text)]
    if not urls:
        return []
    rest = URL_RE.sub(" ", text)
    start = end = moment = None
    m = RANGE_RE.search(rest)
    if m:
        start, end = to_seconds(m.group(1)), to_seconds(m.group(2))
        if end < start:
            start, end = end, start
        rest = rest[:m.start()] + " " + rest[m.end():]
    else:
        m2 = MOMENT_RE.search(rest)
        if m2:
            moment = to_seconds(m2.group(1))
            rest = rest[:m2.start()] + " " + rest[m2.end():]
    hook_m = HOOK_RE.search(rest)
    credit_m = CREDIT_RE.search(rest)
    count_m = COUNT_RE.search(rest)
    flags = {f.lower() for f in FLAG_RE.findall(rest)}
    out = []
    for url in urls:
        mom = moment
        if start is None and mom is None:
            mom = url_time(url)
        out.append(ClipRequest(
            url=url, start=start, end=end, moment=mom,
            credit="@" + credit_m.group(1) if credit_m else None,
            hook=hook_m.group(1).strip() if hook_m else None,
            layout=next((f for f in LAYOUTS if f in flags), None),
            count=int(count_m.group(1)) if count_m else None,
            lang=next((f for f in sorted(flags) if f in LANGS), None),
            flags=set(flags), raw=" ".join(text.split()),
        ))
    return out


def parse_requests(text: str) -> list[ClipRequest]:
    """Une demande par lien. Un seul lien : tout le texte lui sert d'options."""
    text = (text or "").replace("\r", "")
    if len(URL_RE.findall(text)) <= 1:
        return _parse_block(text)
    out: list[ClipRequest] = []
    for line in text.split("\n"):
        out.extend(_parse_block(line))
    seen, unique = set(), []
    for r in out:
        key = (r.url, r.start, r.end, r.moment)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique
