"""Campagne rémunérée (Dasgas Dom, RUCKUS) : source autorisée, priorité, textes du clip."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper import campaign, hunt, sources
from clipper.config import load_config, load_streamers
from clipper.pipeline import Candidate, build_plan
from clipper.request import ClipRequest

UTC = timezone.utc
DOM = next(s for s in load_streamers() if s.get("kick") == "dasgasd3m")


def kick(i, when, *, game="Grand Theft Auto VI", live="111", views=300, title="Dom laughing", streamer="dasgasd3m"):
    return sources.Found(platform="kick", id=f"k{i}", url=f"https://kick.com/{streamer}/clips/k{i}", title=title,
                         views=views, duration=40.0, created_at=when, streamer=streamer,
                         streamer_name=streamer, game=game, extra={"livestream_id": live, "m3u8": ""})


def test_dom_is_declared_with_his_campaign():
    camp = campaign.of(DOM)
    assert camp["games"] == ["Grand Theft Auto VI"] and campaign.language(DOM) == "en"
    assert campaign.off(DOM.get("music")) and DOM.get("story_ok") is True
    lo, hi = campaign.window(camp)
    assert lo == datetime(2026, 11, 19, 5, 0, tzinfo=UTC) and hi == datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    assert not campaign.is_open(camp, datetime(2026, 11, 19, 16, 59, tzinfo=UTC))
    assert campaign.is_open(camp, datetime(2026, 11, 19, 17, 0, tzinfo=UTC))
    assert campaign.ended(camp, datetime(2026, 12, 20, 8, 0, tzinfo=UTC))
    assert "#dasgasdom3" in campaign.hashtags(DOM) and "#ad" in campaign.hashtags(DOM)


def test_only_his_november_19_gta6_stream_is_taken_and_comes_first():
    cfg = load_config()
    now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    clips = [
        kick(1, datetime(2026, 11, 19, 14, 0, tzinfo=UTC), views=200),            # le stream du 19 : gardé
        kick(2, datetime(2026, 11, 20, 10, 0, tzinfo=UTC), views=900),            # même live après minuit : gardé
        kick(3, datetime(2026, 11, 18, 20, 0, tzinfo=UTC), live="100"),           # la veille : écarté
        kick(4, datetime(2026, 11, 19, 15, 0, tzinfo=UTC), game="Just Chatting"),  # autre jeu : écarté
        kick(5, datetime(2026, 11, 21, 9, 0, tzinfo=UTC), live="222"),            # stream suivant : écarté
        kick(6, datetime(2026, 11, 19, 16, 0, tzinfo=UTC), title="SPOILER Lucia ending"),   # histoire : permise
        sources.Found(platform="twitch", id="t1", url="https://www.twitch.tv/bob/clip/t1", title="Gros fail",
                      views=50000, duration=30.0, created_at=now - timedelta(hours=1), streamer="bob",
                      streamer_name="Bob", language="fr", game="Grand Theft Auto VI"),
    ]
    stats: dict = {}
    ranked = hunt.rank(clips, cfg, [DOM], {}, now=now, stats=stats)
    ids = [f.id for _, f, _, _ in ranked]
    assert ids[:3] == ["k2", "k6", "k1"] and ids[3:] == ["t1"]     # la campagne passe devant
    assert stats == {"avant le stream de la campagne": 1, "autre jeu que celui de la campagne": 1,
                     "après le stream de la campagne": 1}
    assert "campagne" in ranked[0][3]


def test_campaign_stops_after_its_deadline():
    cfg = load_config()
    stats: dict = {}
    late = datetime(2026, 12, 21, tzinfo=UTC)
    assert hunt.rank([kick(1, datetime(2026, 11, 19, 14, 0, tzinfo=UTC))], cfg, [DOM], {}, now=late, stats=stats) == []
    assert stats == {"campagne terminée": 1}


def test_request_for_dom_is_in_english_with_his_credit():
    cfg = load_config()
    req = hunt.to_request(kick(1, datetime(2026, 11, 19, 14, 0, tzinfo=UTC)), DOM, cfg)
    assert req.langs == ["en"] and req.lang == "en" and req.credit == "dasgasd3m on Kick"


def test_story_moment_gets_a_spoiler_warning_and_the_required_caption():
    cfg = load_config()
    ev = {"score": 9, "start": 2, "end": 30, "hook": "LUCIA DIES IN FRONT OF DOM",
          "title": "Dasgas Dom reacts to Lucia's death", "description": "Dom can't believe what just happened.",
          "risks": ["spoiler"], "tags": ["gta6"]}
    plan = build_plan(Candidate(Path("a.mp4"), 0.0, 40.0, 20.0, 5.0, 0.0), ev, [20.0],
                      ClipRequest(url="https://kick.com/dasgasd3m/clips/k1", lang="en"),
                      {"title": "x", "webpage_url": "https://kick.com/dasgasd3m/clips/k1"}, cfg, DOM,
                      "kick.com/dasgasd3m", 0)
    assert plan.hook == "SPOILER WARNING" and plan.title.endswith("(SPOILER)")
    assert {"noteaser", "nomusic"} <= plan.flags
    d = plan.description
    assert d.startswith("⚠️ Spoiler warning") and "kick.com/dasgasd3m" in d and "#ad #dasgasdom3" in d
    assert "Source :" not in d and "#Shorts" not in d


def test_ordinary_campaign_clip_keeps_the_ai_hook():
    cfg = load_config()
    ev = {"score": 8, "hook": "DOM FINDS A GTA 6 GLITCH", "title": "Dasgas Dom finds a GTA 6 glitch",
          "description": "The car flies away.", "risks": []}
    plan = build_plan(Candidate(Path("a.mp4"), 0.0, 30.0, 10.0, 5.0, 0.0), ev, [10.0],
                      ClipRequest(url="https://kick.com/dasgasd3m/clips/k2"), {"title": "x"}, cfg, DOM,
                      "kick.com/dasgasd3m", 0)
    assert plan.hook == "DOM FINDS A GTA 6 GLITCH" and "SPOILER" not in plan.title
    assert {"nomusic", "nosfx", "noteaser"} <= plan.flags      # ni musique, ni bruitage, ni aperçu (brief)


def test_brief_reaches_the_ai():
    text = campaign.brief_for_ai(DOM)
    assert "Dasgas Dom" in text and "spoiler" in text and "explicite" in text
    assert campaign.brief_for_ai({}) == ""


def test_kick_channel_pages_until_the_campaign_window(monkeypatch):
    pages = {
        "0": {"clips": [{"id": "a", "created_at": "2026-11-20T10:00:00Z", "livestream_id": 111,
                         "channel": {"slug": "dasgasd3m"}}], "nextCursor": "p2"},
        "p2": {"clips": [{"id": "b", "created_at": "2026-11-18T10:00:00Z", "channel": {"slug": "dasgasd3m"}}],
               "nextCursor": "p3"},
        "p3": {"clips": [{"id": "c", "created_at": "2026-11-10T10:00:00Z", "channel": {"slug": "dasgasd3m"}}]},
    }
    asked = []

    def fake(url):
        asked.append(url)
        return pages[url.split("cursor=")[1].split("&")[0]]

    monkeypatch.setattr(sources, "_kick_get", fake)
    got = sources.kick_channel_clips("dasgasd3m", period="30d", sort="date", pages=6,
                                     since=datetime(2026, 11, 19, 5, 0, tzinfo=UTC))
    assert [f.id for f in got] == ["a", "b"] and len(asked) == 2      # arrêt une fois la fenêtre dépassée
    assert "sort=date&time=month" in asked[0] and got[0].extra["livestream_id"] == "111"


def test_campaign_only_run_skips_everyone_else(monkeypatch, tmp_path):
    cfg = load_config()
    monkeypatch.setattr(hunt, "SEEN", tmp_path / "seen.json")
    seen_cfg = {}

    def fake_discover(c, streamers):
        seen_cfg["twitch"], seen_cfg["kick"] = c["hunt"]["twitch"]["enabled"], c["hunt"]["kick"]["enabled"]
        seen_cfg["streamers"] = [s["name"] for s in streamers]
        return [], []

    monkeypatch.setattr(hunt, "discover", fake_discover)
    monkeypatch.setattr(hunt.deliver, "step_summary", lambda *a, **k: None)
    monkeypatch.setattr(hunt.deliver, "cleanup_releases", lambda d: None)
    others = [{"name": "Bob", "twitch": "bob"}]
    assert hunt.run_hunt(cfg, others + [DOM], tmp_path, tmp_path, campaign_only=True) == 0
    assert seen_cfg == {"twitch": False, "kick": False, "streamers": ["Dasgas Dom"]}


def test_gallery_lists_running_campaigns_as_sections(tmp_path):
    import json

    from clipper import gallery
    src = tmp_path / "clips"
    src.mkdir()
    (src / "a.mp4").write_bytes(b"\x00")
    (src / "a.json").write_text(json.dumps({"file": "a.mp4", "go": True, "title": "A",
                                            "made_at": "2026-11-19T18:00:00+00:00",
                                            "campaign": campaign.card(DOM)}), encoding="utf-8")
    gallery.build(tmp_path / "site", load_config(), from_dir=src)
    data = json.loads((tmp_path / "site" / "clips.json").read_text())
    assert [k["name"] for k in data["campaigns"]] == ["Dom GTA 6 (RUCKUS)"]
    assert data["clips"][0]["campaign"]["name"] == "Dom GTA 6 (RUCKUS)"
    over = dict(DOM, campaign=dict(campaign.of(DOM), closes="2026-11-01T00:00:00Z"))
    assert gallery.active_campaigns([over, {"name": "Bob"}]) == []          # terminée : plus de section


def test_dom_brief_disclosure_length_and_refused_music(tmp_path):
    import subprocess

    from clipper import verify
    assert campaign.caption(DOM, "Dasgas Dom fails a mission", "D") == "Paid partnership · Dasgas Dom fails a mission\n\nD"
    cfg = load_config()
    tuned = campaign.tuned(cfg, DOM)
    assert (tuned["detection"]["clip_min_s"], tuned["detection"]["clip_max_s"]) == (20, 60)
    assert cfg["detection"]["clip_min_s"] == 15                      # réglage général inchangé
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=6",
                    "-f", "lavfi", "-i", "sine=f=300:d=6", "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
    ok = verify.verify(clip, facts={"captions": 3}, risks=["musique"], cfg=cfg, rights_ok=True)
    assert ok["go"]                                                   # ailleurs : simple remarque
    refused = verify.verify(clip, facts={"captions": 3}, risks=["musique"], cfg=cfg, rights_ok=True,
                            block=set(DOM["strict_risks"]))
    assert not refused["go"] and "refusé par la campagne" in refused["problems"][0]
