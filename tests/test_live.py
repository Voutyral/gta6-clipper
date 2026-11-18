"""Veille des lives, rediffusions découpées par l'outil, et campagne Tomo (YouTube)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from clipper import campaign, captions, hunt, live, sources
from clipper.config import load_config, load_streamers

UTC = timezone.utc
STREAMERS = load_streamers()
DOM = next(s for s in STREAMERS if s.get("kick") == "dasgasd3m")
TOMO = next(s for s in STREAMERS if s.get("youtube") == "@Tomovids")
T0 = datetime(2026, 11, 19, 14, 0, tzinfo=UTC)


def test_watch_starts_at_go_live_then_every_hour_then_after_the_end():
    state: dict = {}
    trig, state = live.decide({"Dasgas Dom": False}, state, T0)
    assert trig == []                                                   # hors ligne : rien
    trig, state = live.decide({"Dasgas Dom": True}, state, T0 + timedelta(minutes=15))
    assert trig == [("Dasgas Dom", "début du live")]
    trig, state = live.decide({"Dasgas Dom": True}, state, T0 + timedelta(minutes=30))
    assert trig == []                                                   # moins d'une heure après
    trig, state = live.decide({"Dasgas Dom": True}, state, T0 + timedelta(minutes=75))
    assert trig == [("Dasgas Dom", "chasse de l'heure pendant le live")]
    trig, state = live.decide({"Dasgas Dom": None}, state, T0 + timedelta(minutes=90))
    assert trig == [] and state["Dasgas Dom"]["live"] is True           # statut inconnu : rien ne change
    trig, state = live.decide({"Dasgas Dom": False}, state, T0 + timedelta(minutes=105))
    assert trig == [("Dasgas Dom", "fin du live")]
    trig, state = live.decide({"Dasgas Dom": False}, state, T0 + timedelta(minutes=165))
    assert trig == [("Dasgas Dom", "après le live : clips tardifs et rediffusion")]
    trig, state = live.decide({"Dasgas Dom": False}, state, T0 + timedelta(hours=9))
    assert trig == []                                                   # plus de 6 h après la fin


def test_watch_only_follows_campaigns_with_an_open_window():
    names = lambda now: [s["name"] for s in live.watched(STREAMERS, now)]
    assert names(datetime(2026, 11, 18, 12, 0, tzinfo=UTC)) == []       # trop tôt
    assert names(T0) == ["Dasgas Dom", "Tomo"]
    assert names(datetime(2026, 11, 23, tzinfo=UTC)) == ["Tomo"]         # le stream du 19 de Dom est loin


def test_kick_replays_are_read(monkeypatch):
    monkeypatch.setattr(sources, "_kick_get", lambda url: [{
        "id": 111, "session_title": "GTA 6 DAY ONE", "start_time": "2026-11-19 14:00:00", "duration": 3600000,
        "is_live": False, "views": 5000, "source": "https://stream.kick.com/x/master.m3u8",
        "categories": [{"name": "Grand Theft Auto VI"}], "video": {"uuid": "b4b1c2d3-0000-0000-0000-000000000001"}}])
    v = sources.kick_channel_videos("dasgasd3m")[0]
    assert v.url == "https://kick.com/video/b4b1c2d3-0000-0000-0000-000000000001" and v.duration == 3600
    assert v.extra["vod"] and v.extra["livestream_id"] == "111" and v.game == "Grand Theft Auto VI"
    assert v.created_at == datetime(2026, 11, 19, 14, 0, tzinfo=UTC)        # heure Kick sans fuseau = UTC


def test_youtube_lives_of_tomo_are_listed(monkeypatch):
    from clipper import fetch
    monkeypatch.setattr(fetch, "list_entries", lambda url, limit=8, extra=None: [
        {"id": "abc", "title": "GTA 6 DAY 1 - FIRST TIME PLAYING", "duration": 14000, "timestamp": 1763560000,
         "live_status": "was_live"},
        {"id": "def", "title": "Minecraft but", "duration": 600, "timestamp": 1763560000}] if "streams" in url else [])
    got = sources.youtube_channel_videos("@Tomovids")
    assert [f.id for f in got] == ["abc", "def"] and got[0].game == "Grand Theft Auto VI" and got[1].game == ""
    assert got[0].streamer == "tomovids" and got[0].url.endswith("watch?v=abc")


def test_replays_are_clipped_after_the_viewer_clips_and_never_while_live():
    cfg = load_config()
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    clip = sources.Found(platform="kick", id="k1", url="https://kick.com/dasgasd3m/clips/k1", title="lol", views=300,
                         duration=40, created_at=T0, streamer="dasgasd3m", streamer_name="dasgasd3m",
                         game="Grand Theft Auto VI", extra={"livestream_id": "111"})
    vod = sources.Found(platform="kick", id="vod-1", url="https://kick.com/video/x", title="GTA 6", views=9000,
                        duration=4 * 3600, created_at=T0, streamer="dasgasd3m", streamer_name="dasgasd3m",
                        game="Grand Theft Auto VI", extra={"vod": True, "livestream_id": "111", "live": False})
    on_air = sources.Found(platform="kick", id="vod-2", url="https://kick.com/video/y", title="GTA 6", views=1,
                           duration=0, created_at=T0, streamer="dasgasd3m", streamer_name="dasgasd3m",
                           game="Grand Theft Auto VI", extra={"vod": True, "livestream_id": "111", "live": True})
    stats: dict = {}
    ranked = hunt.rank([vod, clip, on_air], cfg, [DOM], {}, now=now, stats=stats)
    assert [f.id for _, f, _, _ in ranked] == ["k1", "vod-1"]
    assert "rediffusion" in ranked[1][3] and stats == {"live encore en cours (la rediffusion viendra après)": 1}
    req = hunt.to_request(vod, DOM, cfg)
    assert req.count == 6 and req.meta["duration"] == 4 * 3600 and req.langs == ["en"]


def test_replay_that_cannot_be_downloaded_yet_is_retried(monkeypatch, tmp_path):
    cfg = load_config()
    monkeypatch.setattr(hunt, "SEEN", tmp_path / "seen.json")
    vod = sources.Found(platform="youtube", id="abc", url="https://www.youtube.com/watch?v=abc", title="GTA 6 day 1",
                        views=10, duration=14000, created_at=T0, streamer="tomovids", streamer_name="@Tomovids",
                        game="Grand Theft Auto VI", extra={"vod": True, "live": False, "dated": True})
    monkeypatch.setattr(hunt, "discover", lambda c, s: ([vod], []))

    def blocked(*a, **k):
        raise hunt.fetch.FetchError("Téléchargement impossible.", "YouTube bloque")

    monkeypatch.setattr(hunt, "process_request", blocked)
    for name in ("step_summary", "cleanup_releases", "open_issue"):
        monkeypatch.setattr(hunt.deliver, name, lambda *a, **k: None)
    monkeypatch.setattr(hunt.deliver, "pages_url", lambda: "")
    hunt.run_hunt(cfg, [TOMO], tmp_path, tmp_path)
    assert json.loads((tmp_path / "seen.json").read_text()) == {}          # rien de marqué : nouvel essai


def test_tomo_clip_ends_with_the_required_call_to_action():
    cfg = load_config()
    ass = captions.build_ass([], hook="TOMO FAILS", context="", credit="Tomo · @Tomovids", total=20.0, cfg=cfg,
                             cta=campaign.cta(TOMO))
    line = next(x for x in ass.splitlines() if "SUBSCRIBE" in x)
    assert line.startswith("Dialogue: 6,0:00:17.20,0:00:20.00,Hook")
    tags = campaign.hashtags(TOMO, "seed")
    assert tags[:3] == ["#ad", "#tomovids", "#gta6"] and len(tags) == 6
    assert campaign.hashtags(TOMO, "seed") == tags                         # même clip, mêmes hashtags
    assert {tuple(campaign.hashtags(TOMO, str(i))) for i in range(10)} != {tuple(tags)}   # ils varient
