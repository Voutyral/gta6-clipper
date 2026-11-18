"""Sonde temporaire : quelles sources Twitch et Kick répondent depuis GitHub ?"""
import json
import re
import subprocess
import sys

import requests

CID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL = "https://gql.twitch.tv/gql"
H = {"Client-Id": CID, "Content-Type": "application/json"}


def gql(name, query, variables=None):
    try:
        r = requests.post(GQL, headers=H, json={"query": query, "variables": variables or {}}, timeout=30)
        body = r.text
        print(f"\n### {name} -> HTTP {r.status_code}\n{body[:900]}")
    except Exception as exc:
        print(f"\n### {name} -> {exc}")


NODE = "slug title viewCount durationSeconds createdAt url broadcaster { login displayName }"
for crit in ("{period: LAST_DAY, sort: VIEWS_DESC}", "{filter: LAST_DAY}", "{filter: LAST_DAY, languages: [FR]}",
             "{period: LAST_DAY, sort: VIEWS_DESC, languages: [FR]}"):
    gql(f"game clips {crit}", f'query {{ game(name: "Grand Theft Auto V") {{ id name clips(first: 3, criteria: {crit}) {{ edges {{ node {{ {NODE} }} }} }} }} }}')
gql("node extra fields", 'query { game(name: "Grand Theft Auto V") { clips(first: 2, criteria: {filter: LAST_DAY}) { edges { node { slug language game { name displayName } broadcaster { login broadcastSettings { language } } thumbnailURL } } } } }')
gql("GTA VI game", 'query { game(name: "Grand Theft Auto VI") { id name displayName viewersCount } }')
gql("user clips", f'query {{ user(login: "sentry") {{ clips(first: 3, criteria: {{filter: LAST_WEEK}}) {{ edges {{ node {{ {NODE} }} }} }} }} }}')

# Requêtes enregistrées (persisted) utilisées par le site Twitch : hash lu dans un paquet public.
try:
    meta = requests.get("https://data.jsdelivr.com/v1/package/npm/twitch-gql-queries", timeout=30).json()
    ver = meta.get("tags", {}).get("latest")
    print("\ntwitch-gql-queries", ver)
    files = requests.get(f"https://data.jsdelivr.com/v1/package/npm/twitch-gql-queries@{ver}/flat", timeout=30).json()
    names = [f["name"] for f in files.get("files", []) if f["name"].endswith(".js")]
    print("fichiers js :", names[:20])
    found = {}
    for n in names:
        if not re.search(r"(index|queries|clip)", n, re.I):
            continue
        txt = requests.get(f"https://cdn.jsdelivr.net/npm/twitch-gql-queries@{ver}{n}", timeout=30).text
        for m in re.finditer(r"ClipsCards__(Game|User)[\s\S]{0,400}?([0-9a-f]{64})", txt):
            found.setdefault(m.group(1), m.group(2))
    print("hashes :", found)
    if "Game" in found:
        for crit in ({"filter": "LAST_DAY", "languages": ["FR"]}, {"filter": "LAST_DAY", "languages": []}):
            body = {"operationName": "ClipsCards__Game", "variables": {"categorySlug": "grand-theft-auto-v", "gameName": "Grand Theft Auto V", "limit": 3, "criteria": crit},
                    "extensions": {"persistedQuery": {"version": 1, "sha256Hash": found["Game"]}}}
            r = requests.post(GQL, headers=H, json=body, timeout=30)
            print(f"\n### persisted ClipsCards__Game {crit} -> HTTP {r.status_code}\n{r.text[:900]}")
except Exception as exc:
    print("persisted : erreur", exc)

# yt-dlp
for url in ("https://www.twitch.tv/sentry/clips?filter=clips&range=7d",
            "https://www.twitch.tv/directory/category/grand-theft-auto-v/clips?range=24hr"):
    p = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", "--playlist-end", "3", "--no-warnings", url],
                       capture_output=True, text=True)
    out = p.stdout
    try:
        d = json.loads(out)
        ents = [(e.get("title"), e.get("view_count"), e.get("url")) for e in (d.get("entries") or [])][:3]
        print(f"\n### yt-dlp {url} -> ok {ents}")
    except Exception:
        print(f"\n### yt-dlp {url} -> code {p.returncode} {p.stderr[-400:]}")

# Kick
try:
    from curl_cffi import requests as creq
    for url in ("https://kick.com/api/v2/channels/xqc/clips?cursor=0&sort=view&time=day",
                "https://kick.com/api/v2/clips?cursor=0&sort=view&time=day",
                "https://kick.com/api/v1/subcategories/grand-theft-auto-v",
                "https://kick.com/api/v1/subcategories/gta-v",
                "https://kick.com/api/v2/categories/grand-theft-auto-v/clips?cursor=0&sort=view&time=day"):
        try:
            r = creq.get(url, impersonate="chrome", timeout=30)
            print(f"\n### kick {url} -> HTTP {r.status_code} {r.headers.get('content-type')} {r.text[:500]!r}")
        except Exception as exc:
            print(f"\n### kick {url} -> {exc}")
except Exception as exc:
    print("curl_cffi absent", exc)
