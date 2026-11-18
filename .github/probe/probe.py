"""Sonde temporaire n°2 : détails Kick."""
import json
import subprocess
import sys

from curl_cffi import requests as creq


def get(url):
    r = creq.get(url, impersonate="chrome", timeout=30)
    return r.status_code, r.headers.get("content-type"), r.text


for slug in ("grand-theft-auto-vi", "gta-vi", "grand-theft-auto-6", "gta-6"):
    s, ct, t = get(f"https://kick.com/api/v1/subcategories/{slug}")
    print(f"### kick sous-catégorie {slug} -> {s} {t[:160]!r}")

s, ct, t = get("https://kick.com/api/v2/categories/grand-theft-auto-v/clips?cursor=0&sort=view&time=day")
data = json.loads(t)
clips = data.get("clips") or []
print("### nombre de clips GTA V Kick :", len(clips), "| clés racine :", list(data.keys()))
if clips:
    c = clips[0]
    print("### clés d'un clip :", list(c.keys()))
    print("### channel :", json.dumps(c.get("channel"))[:600])
    print("### category :", json.dumps(c.get("category"))[:300])
    print("### top 5 :", [(x.get("title"), x.get("views"), x.get("duration"), (x.get("channel") or {}).get("slug")) for x in clips[:5]])
    slug = (c.get("channel") or {}).get("slug")
    page = f"https://kick.com/{slug}/clips/{c['id']}"
    s2, ct2, t2 = get(f"https://kick.com/api/v2/channels/{slug}")
    try:
        ch = json.loads(t2)
        print("### clés chaîne :", list(ch.keys()))
        for k in ("livestream", "previous_livestreams", "user", "recent_categories"):
            v = ch.get(k)
            print(f"### chaîne.{k} :", json.dumps(v)[:400] if v is not None else None)
    except Exception as exc:
        print("### chaîne illisible", s2, exc, t2[:200])
    for url in (page, c.get("clip_url")):
        p = subprocess.run([sys.executable, "-m", "yt_dlp", "-J", "--no-warnings", url], capture_output=True, text=True)
        if p.returncode == 0:
            d = json.loads(p.stdout)
            print(f"### yt-dlp {url} -> ok titre={d.get('title')!r} durée={d.get('duration')} chaîne={d.get('channel') or d.get('uploader')} formats={len(d.get('formats') or [])}")
        else:
            print(f"### yt-dlp {url} -> erreur {p.stderr[-300:]}")
    p = subprocess.run([sys.executable, "-m", "yt_dlp", "-f", "b", "-o", "/tmp/k.%(ext)s", "--no-warnings", page], capture_output=True, text=True)
    print("### téléchargement page :", p.returncode, p.stderr[-200:])
    subprocess.run(["ls", "-la", "/tmp"], check=False)
