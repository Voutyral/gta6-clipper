"""Chasse en français et en anglais : recherche par langue, alternance, textes dans la langue du clip."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper import hunt, sources
from clipper.config import load_config, load_streamers
from clipper.pipeline import Candidate, build_plan, text_lang
from clipper.request import ClipRequest

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def tw(i, lang, views, hours=1.0):
    return sources.Found(platform="twitch", id=f"{lang}{i}", url=f"https://www.twitch.tv/s{lang}{i}/clip/x",
                         title="clip", views=views, duration=30.0, created_at=NOW - timedelta(hours=hours),
                         streamer=f"s{lang}{i}", streamer_name=f"S{i}", language=lang, game="Grand Theft Auto VI")


def test_french_and_english_clips_alternate():
    cfg = load_config()
    clips = [tw(1, "en", 5000), tw(2, "en", 4000), tw(3, "en", 3000), tw(1, "fr", 400), tw(2, "fr", 300),
             tw(3, "fr", 90)]                                      # peu de vues : passe après les autres
    ranked = hunt.rank(clips, cfg, [], {}, now=NOW)
    assert [f.id for _, f, _, _ in ranked] == ["en1", "fr1", "en2", "fr2", "en3", "fr3"]


def test_french_only_hunt_keeps_the_old_order():
    cfg = load_config()
    cfg["hunt"]["require_languages"] = ["fr"]
    ranked = hunt.rank([tw(1, "en", 5000), tw(1, "fr", 400), tw(2, "fr", 300)], cfg, [], {}, now=NOW)
    assert [f.id for _, f, _, _ in ranked] == ["fr1", "fr2"]


def test_twitch_is_searched_once_per_language(monkeypatch):
    cfg = load_config()
    asked = []
    monkeypatch.setattr(sources, "twitch_game_clips", lambda game, **k: asked.append((game, k["languages"])) or [])
    monkeypatch.setattr(sources, "kick_category_clips", lambda *a, **k: [])
    sources.discover(cfg, [])
    assert ("Grand Theft Auto VI", ["fr"]) in asked and ("Grand Theft Auto VI", ["en"]) in asked
    assert len(asked) == 4


def test_text_language_follows_the_clip():
    cfg = load_config()
    req = ClipRequest(url="https://x.tv/v")
    assert text_lang(req, {}, cfg, "en") == "en" and text_lang(req, {}, cfg, "fr") == "fr"
    assert text_lang(req, {}, cfg, "es") == "fr" and text_lang(req, {}, cfg, "") == "fr"
    assert text_lang(ClipRequest(url="x", lang="fr"), {}, cfg, "en") == "fr"      # #fr dans la demande
    dom = next(s for s in load_streamers() if s.get("kick") == "dasgasd3m")
    assert text_lang(req, dom, cfg, "fr") == "en"                                 # fiche du streamer
    cfg["metadata"]["language"] = "fr"
    assert text_lang(req, {}, cfg, "en") == "fr"                                  # langue imposée


def test_english_clip_gets_english_texts():
    cfg = load_config()
    c = Candidate(Path("a.mp4"), 0.0, 30.0, 12.0, 3.0, 0.0, lang="en")
    plan = build_plan(c, None, [12.0], ClipRequest(url="https://x.tv/v"), {"title": "Heist", "channel": "Bob",
                      "webpage_url": "https://x.tv/v"}, cfg, {}, "@Bob", 0)
    assert plan.text_lang == "en" and plan.hook == "WAIT FOR IT"
    assert plan.title == "Bob: Heist" and "Source: @Bob" in plan.description
    fr = build_plan(Candidate(Path("a.mp4"), 0.0, 30.0, 12.0, 3.0, 0.0, lang="fr"), None, [12.0],
                    ClipRequest(url="https://x.tv/v"), {"title": "Braquage", "channel": "Bob"}, cfg, {}, "@Bob", 0)
    assert fr.text_lang == "fr" and fr.hook == "ATTENDS LA FIN" and fr.title == "Bob : Braquage"
