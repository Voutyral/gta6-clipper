"""Tests de la chasse et de la galerie, sans réseau."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper import gallery, hunt, sources
from clipper.config import load_config
from clipper.pipeline import facecam_box

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def found(i=1, views=500, hours=3.0, streamer="bob", lang="fr", dur=30.0, title="Gros fail", platform="twitch"):
    return sources.Found(platform=platform, id=f"c{i}", url=f"https://www.twitch.tv/{streamer}/clip/c{i}",
                         title=title, views=views, duration=dur, created_at=NOW - timedelta(hours=hours),
                         streamer=streamer, streamer_name=streamer.title(), language=lang,
                         game="Grand Theft Auto VI")


def test_twitch_node_is_parsed():
    node = {"slug": "Abc", "title": "OH LA LA", "viewCount": 749, "durationSeconds": 43,
            "createdAt": "2026-11-17T22:11:45Z", "url": "https://www.twitch.tv/x/clip/Abc", "language": "FR",
            "game": {"name": "Grand Theft Auto V"}, "broadcaster": {"login": "mastersnakou", "displayName": "MasterSnakou"}}
    f = sources._twitch_node(node)
    assert (f.key, f.views, f.duration, f.language, f.streamer, f.game) == \
        ("twitch:Abc", 749, 43.0, "fr", "mastersnakou", "Grand Theft Auto V")
    assert f.created_at.tzinfo is not None
    assert sources._twitch_node({}) is None


def test_twitch_query_carries_period_and_languages(monkeypatch):
    seen = {}

    def fake(query):
        seen["q"] = query
        return {"game": {"clips": {"edges": [{"node": {"slug": "A", "broadcaster": {"login": "a"}}}]}}}

    monkeypatch.setattr(sources, "_gql", fake)
    out = sources.twitch_game_clips("Grand Theft Auto VI", period="24hr", languages=["fr", "en"], first=5)
    assert len(out) == 1 and out[0].game == "Grand Theft Auto VI"
    assert 'game(name: "Grand Theft Auto VI")' in seen["q"]
    assert "filter: LAST_DAY, languages: [FR, EN]" in seen["q"] and "first: 5" in seen["q"]


def test_kick_clip_is_parsed():
    raw = {"id": "clip_01X", "title": "Capped reaction", "views": 5072, "duration": 179,
           "created_at": "2026-11-17T10:00:00Z", "clip_url": "https://clips.kick.com/x/playlist.m3u8",
           "thumbnail_url": "https://clips.kick.com/x/thumbnail.webp",
           "channel": {"username": "Capped", "slug": "capped"}, "category": {"name": "Grand Theft Auto V (GTA)"}}
    f = sources._kick_clip(raw)
    assert f.url == "https://kick.com/capped/clips/clip_01X" and f.language == ""
    assert f.extra["m3u8"].endswith("playlist.m3u8") and f.views == 5072


def test_discover_keeps_going_when_a_source_fails(monkeypatch):
    cfg = load_config()

    def boom(*a, **k):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(sources, "twitch_game_clips", lambda *a, **k: [found(1), found(1)])
    monkeypatch.setattr(sources, "kick_category_clips", boom)
    got, errors = sources.discover(cfg, [])
    assert [f.key for f in got] == ["twitch:c1"]          # doublons fusionnés
    assert errors and all("HTTP 403" in e for e in errors)


def test_rank_prefers_fast_risers_and_applies_filters():
    cfg = load_config()
    clips = [
        found(1, views=600, hours=20),                    # beaucoup de vues mais vieux
        found(2, views=400, hours=1, streamer="alice"),   # récent qui décolle
        found(3, views=90, hours=1, streamer="carl"),     # sous le seuil : passe après les autres
        found(8, views=20, hours=1, streamer="gus"),      # sous le plancher : écarté
        found(4, views=900, hours=2, streamer="dan", title="GTA 6 ENDING spoiler"),
        found(5, views=900, hours=2, streamer="eve", dur=200),
        found(6, views=900, hours=2, streamer="fred", lang="en"),   # mauvaise langue
        found(7, views=800, hours=2, streamer="seen"),
    ]
    ranked = hunt.rank(clips, cfg, [], {"twitch:c7": {"d": "2026-11-20", "s": "seen", "ok": True}}, now=NOW)
    assert [f.id for _, f, _, _ in ranked] == ["c2", "c1", "c3"]
    assert "peu de vues" in ranked[2][3]


def test_kick_title_in_another_language_is_dropped_before_download():
    cfg = load_config()
    clips = [found(1, views=900, hours=2, streamer="saab", lang="", platform="kick",
                   title="Dawson sees Chatty's missed calls to Ray Mond and the boys"),
             found(2, views=900, hours=2, streamer="momo", lang="", platform="kick", title="OH LA DINGUERIE")]
    stats: dict = {}
    ranked = hunt.rank(clips, cfg, [], {}, now=NOW, stats=stats)
    assert [f.streamer for _, f, _, _ in ranked] == ["momo"]
    assert stats == {"titre dans une autre langue": 1}


def test_rank_caps_per_streamer_and_unknown_language():
    cfg = load_config()
    clips = [found(i, views=1000 - i, hours=1, streamer="bob") for i in range(1, 5)]
    clips += [found(10 + i, views=5000, hours=1, streamer=f"k{i}", lang="", platform="kick") for i in range(3)]
    ranked = hunt.rank(clips, cfg, [], {}, now=NOW)
    names = [f.streamer for _, f, _, _ in ranked]
    assert names.count("bob") == 2                        # max_per_streamer_day
    assert sum(1 for n in names if n.startswith("k")) == 1  # une seule langue inconnue
    assert ranked[0][1].streamer == "bob"                 # pénalité langue inconnue


def test_rank_boosts_allowlisted_streamers():
    cfg = load_config()
    streamers = [{"name": "Zed", "twitch": "zed", "credit": "@ZedTV"}]
    ranked = hunt.rank([found(1, views=600, hours=1), found(2, views=400, hours=1, streamer="zed")],
                       cfg, streamers, {}, now=NOW)
    assert ranked[0][1].streamer == "zed" and "streamer suivi" in ranked[0][3]
    req = hunt.to_request(ranked[0][1], ranked[0][2], cfg)
    assert req.credit == "@ZedTV" and req.min_score == 6 and req.langs is None
    assert req.meta["views"] == 400 and req.game_hint == "Grand Theft Auto VI"


def test_facecam_box_validation():
    assert facecam_box([700, 0, 1000, 240]) == {"x": 0.0, "y": 0.7, "w": 0.24, "h": 0.3}
    assert facecam_box([0, 0, 1000, 1000]) is None        # toute l'image
    assert facecam_box([10, 10, 12, 12]) is None          # minuscule
    assert facecam_box([0, 0, 900, 50]) is None           # bande verticale
    assert facecam_box(None) is None and facecam_box("non") is None


def test_hunt_loop_produces_marks_seen_and_reports(monkeypatch, tmp_path):
    cfg = load_config()
    monkeypatch.setattr(hunt, "SEEN", tmp_path / "seen.json")
    monkeypatch.setattr(hunt, "discover", lambda c, s: ([found(1, hours=1), found(2, hours=1, streamer="alice")], []))
    calls, delivered = [], []

    class R:
        def __init__(self, go):
            self.go, self.title, self.meta = go, "t", {}

    def fake_process(req, cfg, streamers, work, outdir, idx):
        calls.append(req.url)
        return [R(go=idx == 0)]

    monkeypatch.setattr(hunt, "process_request", fake_process)
    monkeypatch.setattr(hunt, "deliver_result", lambda res, cfg: delivered.append(res))
    issues = []
    monkeypatch.setattr(hunt.deliver, "open_issue", lambda *a, **k: issues.append((a, k)))
    monkeypatch.setattr(hunt.deliver, "markdown_report", lambda *a, **k: "rapport")
    monkeypatch.setattr(hunt.deliver, "pages_url", lambda: "")
    monkeypatch.setattr(hunt.deliver, "cleanup_releases", lambda d: None)
    assert hunt.run_hunt(cfg, [], tmp_path, tmp_path) == 0
    assert len(calls) == 2 and len(delivered) == 1        # le NO-GO n'est pas livré
    seen = json.loads((tmp_path / "seen.json").read_text())
    assert seen["twitch:c1"]["ok"] is True and seen["twitch:c2"]["ok"] is False
    assert issues and issues[0][1]["label"] == "chasse"
    # Second passage : plus rien de neuf.
    calls.clear()
    hunt.run_hunt(cfg, [], tmp_path, tmp_path)
    assert calls == []


def test_gallery_builds_from_local_cards(tmp_path):
    src = tmp_path / "clips"
    src.mkdir()
    for i, go in enumerate([True, False, True]):
        (src / f"c{i}.mp4").write_bytes(b"\x00" * 10)
        (src / f"c{i}.jpg").write_bytes(b"\xff\xd8")
        (src / f"c{i}.json").write_text(json.dumps({
            "file": f"c{i}.mp4", "thumb": f"c{i}.jpg", "go": go, "title": f"Clip {i}",
            "made_at": f"2026-11-20T1{i}:00:00+00:00"}), encoding="utf-8")
    site = tmp_path / "site"
    n = gallery.build(site, load_config(), from_dir=src)
    data = json.loads((site / "clips.json").read_text())
    assert n == 2 and [c["title"] for c in data["clips"]] == ["Clip 2", "Clip 0"]
    assert data["clips"][0]["src"] == "c/c2.mp4" and (site / "c" / "c2.mp4").exists()
    assert not (site / "c" / "c1.mp4").exists()
    for f in ("index.html", "manifest.webmanifest", "icon-180.png", ".nojekyll", "fonts/Anton-Regular.ttf"):
        assert (site / f).exists(), f


@pytest.mark.parametrize("old", [{"twitch:x": "2026-11-19"}, {}])
def test_seen_file_migrates_old_format(tmp_path, monkeypatch, old):
    monkeypatch.setattr(hunt, "SEEN", tmp_path / "seen.json")
    (tmp_path / "seen.json").write_text(json.dumps(old))
    data = hunt.load_seen()
    assert all(isinstance(v, dict) and "d" in v for v in data.values())
    hunt.save_seen(data)
    assert json.loads((tmp_path / "seen.json").read_text()) is not None


def test_sidecar_card_is_complete(tmp_path):
    import subprocess

    from clipper.pipeline import Result, write_sidecars

    clip = tmp_path / "x.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192:d=2",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    res = Result(path=clip, title="T", description="D\n\n#GTA6", tags=["a"], hook="H", go=True, problems=[],
                 notes=[], duration=2.0, size_mb=0.1, credit="@bob", source_url="https://x", facts={},
                 meta={"ai_score": 8.0, "platform": "twitch", "views": 400})
    side, thumb = write_sidecars(res)
    card = json.loads(Path(side).read_text())
    assert card["file"] == "x.mp4" and card["thumb"] == "x.jpg" and thumb.exists()
    assert card["ai_score"] == 8.0 and card["platform"] == "twitch" and card["go"] is True


def test_hashtags_follow_the_real_game():
    from clipper.pipeline import hashtags_for
    cfg = load_config()
    assert hashtags_for("Grand Theft Auto V (GTA)", cfg) == ["#GTA5", "#GTAV", "#GTARP", "#Shorts"]
    assert hashtags_for("Grand Theft Auto VI", cfg) == ["#GTA6", "#GTAVI", "#Shorts"]
    assert hashtags_for("", cfg) == cfg["metadata"]["hashtags"]


def test_game_crop_avoids_the_webcam():
    from clipper.render import game_x
    right = game_x(0.74, 0.255)
    assert "0.7400*iw-1080" in right and right.startswith("max(0,min(iw-1080,min(")
    left = game_x(0.0, 0.24)
    assert "max((iw-1080)/2,0.2400*iw)" in left


def test_rank_counts_why_clips_are_dropped():
    cfg = load_config()
    clips = [
        found(1, views=900, hours=3, streamer="a"),
        found(2, views=20, hours=3, streamer="b"),            # pas assez de vues
        found(3, views=900, hours=80, streamer="c"),          # trop ancien
        found(4, views=900, hours=3, streamer="d", dur=300),  # trop long
        found(5, views=900, hours=3, streamer="e", lang="de"),
    ]
    stats: dict = {}
    picked = hunt.rank(clips, cfg, [], {}, now=NOW, stats=stats)
    assert [p[1].id for p in picked] == ["c1"]
    assert stats == {"pas assez de vues": 1, "trop anciens": 1, "durée hors limites": 1, "autre langue": 1}
    assert "1 pas assez de vues" in hunt.describe_stats(stats)


def test_tool_links_are_not_clip_requests():
    from clipper.request import parse_requests
    assert parse_requests("Clip hors sujet.\n\n---\n_Generated by [Claude Code](https://claude.ai/code)_") == []
    reqs = parse_requests("https://www.twitch.tv/x/clip/Abc @x\nvoir https://github.com/Voutyral/gta6-clipper")
    assert [r.url for r in reqs] == ["https://www.twitch.tv/x/clip/Abc"]
