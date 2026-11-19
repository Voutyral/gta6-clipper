"""Veille des lives, rediffusions découpées par l'outil, et campagne sur une chaîne YouTube."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from clipper import campaign, captions, hunt, live, sources
from clipper.config import load_config, load_streamers

UTC = timezone.utc
STREAMERS = load_streamers()
DOM = next(s for s in STREAMERS if s.get("kick") == "dasgasd3m")
# Exemple de campagne YouTube (plus dans streamers.yaml) : appel à l'action et hashtags variés.
YT = {"name": "Exemple", "youtube": "@ExempleChaine", "credit": "@ExempleChaine", "language": "en",
        "cta": "SUBSCRIBE TO YT ON YOUTUBE", "hashtags": ["#ad", "#exemplechaine", "#gta6"],
        "hashtags_mix": ["#exemple", "#gtavi", "#gta", "#gaming", "#funnymoments"],
        "campaign": {"name": "Exemple GTA VI", "footage_from": "2026-11-19T00:00:00-05:00",
                     "games": ["Grand Theft Auto VI"], "vod_clips": 4}}
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
    assert names(T0) == ["Dasgas Dom"]
    assert names(datetime(2026, 11, 23, tzinfo=UTC)) == []               # le stream du 19 de Dom est loin
    assert [s["name"] for s in live.watched([YT], datetime(2026, 11, 23, tzinfo=UTC))] == ["Exemple"]


def test_kick_replays_are_read(monkeypatch):
    monkeypatch.setattr(sources, "_kick_get", lambda url: [{
        "id": 111, "session_title": "GTA 6 DAY ONE", "start_time": "2026-11-19 14:00:00", "duration": 3600000,
        "is_live": False, "views": 5000, "source": "https://stream.kick.com/x/master.m3u8",
        "categories": [{"name": "Grand Theft Auto VI"}], "video": {"uuid": "b4b1c2d3-0000-0000-0000-000000000001"}}])
    v = sources.kick_channel_videos("dasgasd3m")[0]
    assert v.url == "https://kick.com/video/b4b1c2d3-0000-0000-0000-000000000001" and v.duration == 3600
    assert v.extra["vod"] and v.extra["livestream_id"] == "111" and v.game == "Grand Theft Auto VI"
    assert v.created_at == datetime(2026, 11, 19, 14, 0, tzinfo=UTC)        # heure Kick sans fuseau = UTC


def test_youtube_lives_are_listed(monkeypatch):
    from clipper import fetch
    monkeypatch.setattr(fetch, "list_entries", lambda url, limit=8, extra=None: [
        {"id": "abc", "title": "GTA 6 DAY 1 - FIRST TIME PLAYING", "duration": 14000, "timestamp": 1763560000,
         "live_status": "was_live"},
        {"id": "def", "title": "Minecraft but", "duration": 600, "timestamp": 1763560000}] if "streams" in url else [])
    got = sources.youtube_channel_videos("@ExempleChaine")
    assert [f.id for f in got] == ["abc", "def"] and got[0].game == "Grand Theft Auto VI" and got[1].game == ""
    assert got[0].streamer == "exemplechaine" and got[0].url.endswith("watch?v=abc")


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
    assert req.count == int(campaign.of(DOM)["vod_clips"]) and req.meta["duration"] == 4 * 3600 and req.langs == ["en"]


def test_replay_that_cannot_be_downloaded_yet_is_retried(monkeypatch, tmp_path):
    cfg = load_config()
    monkeypatch.setattr(hunt, "SEEN", tmp_path / "seen.json")
    vod = sources.Found(platform="youtube", id="abc", url="https://www.youtube.com/watch?v=abc", title="GTA 6 day 1",
                        views=10, duration=14000, created_at=T0, streamer="exemplechaine", streamer_name="@ExempleChaine",
                        game="Grand Theft Auto VI", extra={"vod": True, "live": False, "dated": True})
    monkeypatch.setattr(hunt, "discover", lambda c, s: ([vod], []))

    def blocked(*a, **k):
        raise hunt.fetch.FetchError("Téléchargement impossible.", "YouTube bloque")

    monkeypatch.setattr(hunt, "process_request", blocked)
    for name in ("step_summary", "cleanup_releases", "open_issue"):
        monkeypatch.setattr(hunt.deliver, name, lambda *a, **k: None)
    monkeypatch.setattr(hunt.deliver, "pages_url", lambda: "")
    hunt.run_hunt(cfg, [YT], tmp_path, tmp_path)
    assert json.loads((tmp_path / "seen.json").read_text()) == {}          # rien de marqué : nouvel essai


def test_campaign_clip_ends_with_its_call_to_action():
    cfg = load_config()
    ass = captions.build_ass([], hook="YT FAILS", context="", credit="Exemple · @ExempleChaine", total=20.0, cfg=cfg,
                             cta=campaign.cta(YT))
    line = next(x for x in ass.splitlines() if "SUBSCRIBE" in x)
    assert line.startswith("Dialogue: 6,0:00:17.20,0:00:20.00,Hook")
    tags = campaign.hashtags(YT, "seed")
    assert tags[:3] == ["#ad", "#exemplechaine", "#gta6"] and len(tags) == 6
    assert campaign.hashtags(YT, "seed") == tags                         # même clip, mêmes hashtags
    assert {tuple(campaign.hashtags(YT, str(i))) for i in range(10)} != {tuple(tags)}   # ils varient


def test_safety_net_hunts_every_two_hours_while_the_stream_window_is_open():
    lo, hi = campaign.window(campaign.of(DOM))
    win = {"Dasgas Dom": (lo, hi)}
    trig, state = live.decide({"Dasgas Dom": None}, {}, lo - timedelta(hours=1), win)
    assert trig == []                                                   # fenêtre pas encore ouverte
    trig, state = live.decide({"Dasgas Dom": None}, state, lo + timedelta(minutes=10), win)
    assert trig == [("Dasgas Dom", "filet de sécurité, fenêtre du stream ouverte, statut illisible")]
    assert "live" not in state["Dasgas Dom"]                            # statut inconnu : l'état du live ne bouge pas
    trig, state = live.decide({"Dasgas Dom": False}, state, lo + timedelta(minutes=70), win)
    assert trig == []                                                   # moins de 2 heures après
    trig, state = live.decide({"Dasgas Dom": False}, state, lo + timedelta(minutes=130), win)
    assert trig == [("Dasgas Dom", "filet de sécurité, fenêtre du stream ouverte")]
    trig, state = live.decide({"Dasgas Dom": True}, state, lo + timedelta(minutes=140), win)
    assert trig == [("Dasgas Dom", "début du live")]                    # le live passe avant le filet
    trig, _ = live.decide({"Dasgas Dom": None}, {}, hi + timedelta(hours=1), win)
    assert trig == []                                                   # fenêtre fermée


def test_watch_runs_continuously_around_the_campaign_stream():
    lo, hi = campaign.window(campaign.of(DOM))
    keep = lambda now, state=None: live.keep_watching(STREAMERS, state or {}, now)
    assert not keep(lo - timedelta(hours=30))                           # trop tôt
    assert keep(lo - timedelta(hours=15))                               # la veille du stream
    assert keep(lo + timedelta(hours=3)) and keep(hi + timedelta(hours=2))
    ended = {"Dasgas Dom": {"live": False, "ended": (hi - timedelta(hours=1)).isoformat()}}
    assert keep(hi + timedelta(hours=4), ended)                         # chasses d'après-live en cours
    assert not keep(hi + timedelta(hours=6), ended)                     # tout est fait
    assert not keep(hi + timedelta(hours=19))                           # trop tard dans tous les cas
    assert not live.keep_watching([YT], {}, lo)                         # sans fin de fenêtre : veille programmée


def test_watch_outputs_tell_the_workflow_what_to_do(monkeypatch, tmp_path):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(live, "STATE", tmp_path / "live.json")
    monkeypatch.setattr(live, "status", lambda s: True)
    lo, _ = campaign.window(campaign.of(DOM))
    live.run_watch(STREAMERS, now=lo - timedelta(hours=10))
    assert out.read_text().split() == ["run=false", "watching=false", "keep=true"]
    out.unlink()
    live.run_watch(STREAMERS, now=lo + timedelta(hours=1))
    assert out.read_text().split() == ["run=true", "watching=true", "keep=true"]
    assert json.loads((tmp_path / "live.json").read_text())["Dasgas Dom"]["live"] is True
