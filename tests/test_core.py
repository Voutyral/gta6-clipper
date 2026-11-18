"""Tests sans réseau : lecture des demandes, repérage, sous-titres, choix des clips.

    python -m pytest -q
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from clipper import ai, captions, detect
from clipper.config import load_config, match_streamer
from clipper.learn import is_learn_request
from clipper.pipeline import Candidate, build_plan, clean_hook, snap_to_words
from clipper.request import ClipRequest, parse_requests, url_time


def test_request_range_credit_flags():
    r = parse_requests('https://www.twitch.tv/videos/123 1:02:30-1:03:10 @Streamer_1 #crop x3 "Il saute"')[0]
    assert r.url == "https://www.twitch.tv/videos/123"
    assert (r.start, r.end) == (3750.0, 3790.0)
    assert r.credit == "@Streamer_1" and r.layout == "crop" and r.count == 3 and r.hook == "Il saute"


def test_request_title_and_body_of_an_issue():
    r = parse_requests("https://clips.twitch.tv/AbcDef \n12:40 à 13:05\n#noteaser #en")[0]
    assert (r.start, r.end) == (760.0, 785.0)
    assert "noteaser" in r.flags and r.lang == "en"


def test_request_single_moment_and_url_time():
    assert parse_requests("https://youtu.be/xyz 12:40")[0].moment == 760.0
    assert parse_requests("https://www.twitch.tv/videos/1?t=1h2m3s")[0].moment == 3723.0
    assert url_time("https://youtu.be/xyz?t=754") == 754.0
    assert url_time("https://youtu.be/xyz") is None


def test_request_handle_in_url_is_not_a_credit():
    r = parse_requests("https://www.youtube.com/@chaine/videos https://youtu.be/abc")
    assert all(x.credit is None for x in r)


def test_request_several_links_one_per_line():
    reqs = parse_requests("https://a.tv/1 0:10-0:40\nhttps://a.tv/2 @bob\nhttps://a.tv/1 0:10-0:40")
    assert [x.url for x in reqs] == ["https://a.tv/1", "https://a.tv/2"]
    assert reqs[1].credit == "@bob" and reqs[1].start is None


def test_request_without_link():
    assert parse_requests("fais-moi un clip stp") == []


def _fake_stream(hop=0.5, total=600.0, bursts=((100, 104), (300, 303), (480, 486))):
    rng = np.random.default_rng(0)
    n = int(total / hop)
    db = -32 + rng.normal(0, 0.8, n)
    for a, b in bursts:
        db[int(a / hop): int(b / hop)] = -8
    return db.astype(np.float32)


def test_windows_find_the_loud_moments():
    wins = detect.find_windows(_fake_stream(), 0.5, 3, 12, 14, 15, 45)
    peaks = sorted(round(w.peak) for w in wins)
    assert len(wins) == 3
    assert all(any(a - 1 <= p <= b + 1 for a, b in ((100, 104), (300, 303), (480, 486))) for p in peaks)
    assert all(15 <= w.end - w.start <= 45 for w in wins)


def test_windows_on_flat_audio_are_spread_out():
    flat = np.full(1200, -30.0, dtype=np.float32)
    wins = detect.find_windows(flat, 0.5, 2, 12, 14, 15, 45)
    assert len(wins) == 2 and wins[0].start < wins[1].start


def test_short_source_is_kept_whole():
    wins = detect.find_windows(np.full(60, -30.0, dtype=np.float32), 0.5, 3, 12, 14, 15, 45)
    assert len(wins) == 1 and wins[0].start == 0.0 and wins[0].end == 30.0


def test_peaks_point_at_the_start_of_the_hit():
    db = np.full(300, -30.0, dtype=np.float32)
    db[120:130] = -6
    peaks = detect.find_peaks(db, 0.1)
    assert peaks and abs(peaks[0] - 12.0) <= 0.3


def _words():
    raw = [("J'ai", 0.5, 0.8), ("vraiment", 0.85, 1.3), ("sauté.", 1.35, 1.9), ("!", 1.9, 1.95),
           ("Regarde", 3.0, 3.4), ("{ça}", 3.45, 3.8)]
    return captions.normalize_words([{"raw": r, "s": s, "e": e} for r, s, e in raw], 10.0)


def test_words_are_cleaned_and_grouped():
    words = _words()
    assert [w["w"] for w in words] == ["J'AI", "VRAIMENT", "SAUTÉ", "REGARDE", "ÇA"]
    groups = captions.group_words(words, 3, 13)
    assert [[w["w"] for w in g] for g in groups] == [["J'AI", "VRAIMENT"], ["SAUTÉ"], ["REGARDE", "ÇA"]]


def test_ass_file_has_hook_credit_and_safe_text():
    cfg = load_config()
    ass = captions.build_ass(_words(), hook="Il saute 😱 du {pont}", context="Braquage raté",
                             credit="Clip : @bob", total=10.0, cfg=cfg)
    assert "IL SAUTE DU (PONT)" in ass and "😱" not in ass
    assert "Clip : @bob" in ass and "Braquage raté" in ass
    assert ass.count("Dialogue: 2,") == 5
    assert "{ça}" not in ass


def test_ass_time_format():
    assert captions.ass_time(3723.456) == "1:02:03.46"


def test_ai_json_parsing_is_tolerant():
    assert ai.parse_json('```json\n{"score": 7}\n```') == {"score": 7}
    assert ai.parse_json('Voici : {"score": 3, "hook": "A"} merci') == {"score": 3, "hook": "A"}
    assert ai.parse_json('[{"score": 5}]') == {"score": 5}
    assert ai.parse_json("pas du json") is None and ai.parse_json(None) is None


def test_no_ai_provider_without_keys(monkeypatch):
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GITHUB_ACTIONS"):
        monkeypatch.delenv(k, raising=False)
    assert ai.providers(load_config()) == []


def test_plan_uses_ai_advice_but_respects_a_locked_range():
    cfg = load_config()
    info = {"title": "Session GTA 6", "webpage_url": "https://x.tv/v", "channel": "Bob"}
    ev = {"score": 8, "start": 5, "end": 30, "hook": "il saute du pont en feu maintenant vraiment",
          "title": "Il saute du pont", "description": "Bob tente le saut.", "tags": ["#gta6", "saut"],
          "risks": ["cinematique", 3]}
    free = build_plan(Candidate(Path("a.mp4"), 100.0, 140.0, 110.0, 4.0, 0.0), ev, [110.0],
                      ClipRequest(url="https://x.tv/v"), info, cfg, {}, "@Bob", 0)
    assert (free.start, free.end) == (105.0, 130.0)
    assert free.hook == "il saute du pont en feu" and free.tags == ["gta6", "saut"]
    assert free.risks == ["cinematique"] and free.score > 8
    assert "Source : @Bob (https://x.tv/v)" in free.description and "#GTA6" in free.description
    locked = build_plan(Candidate(Path("a.mp4"), 100.0, 140.0, None, 1.0, 0.0, locked=True), ev, [],
                        ClipRequest(url="https://x.tv/v", hook="Mon accroche"), info, cfg, {}, "@Bob", 0)
    assert (locked.start, locked.end) == (100.0, 140.0) and locked.hook == "Mon accroche"


def test_plan_without_ai_falls_back_to_simple_rules():
    cfg = load_config()
    plan = build_plan(Candidate(Path("a.mp4"), 0.0, 30.0, 12.0, 3.0, 0.0), None, [12.0],
                      ClipRequest(url="https://x.tv/v"), {"title": "Braquage", "channel": "Bob"},
                      cfg, {}, "@Bob", 1)
    assert plan.hook == clean_hook(cfg["metadata"]["fallback_hooks"][1])
    assert plan.title == "Bob : Braquage" and plan.tags


def test_cut_never_lands_inside_a_word():
    words = [{"s": 9.8, "e": 10.4, "w": "A"}, {"s": 29.9, "e": 30.5, "w": "B"}]
    assert snap_to_words(10.0, 30.0, words, 0.0, 40.0) == (9.72, 30.7)


def test_streamer_lookup_and_learn_keyword():
    streamers = [{"name": "Bob", "twitch": "bob_tv", "credit": "@BobTV"}]
    assert match_streamer(streamers, "", "bob_tv")["name"] == "Bob"
    assert match_streamer(streamers, "@bobtv")["name"] == "Bob"
    assert match_streamer(streamers, "alice") == {}
    assert is_learn_request("Apprends https://youtu.be/x le montage")
    assert not is_learn_request("https://youtu.be/x 1:00-1:30")


def test_whisper_path_gets_an_array_and_cleans_words(tmp_path, monkeypatch):
    import subprocess
    import sys
    import types

    from clipper import transcribe

    wav_src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=4", str(wav_src)],
                   check=True)
    seen = {}

    def word(text, s, e):
        return types.SimpleNamespace(word=text, start=s, end=e, probability=0.9)

    class FakeModel:
        def transcribe(self, audio, **kw):
            seen["type"], seen["dtype"], seen["n"] = type(audio).__name__, str(audio.dtype), audio.shape[0]
            seen["kw"] = kw
            segs = [
                types.SimpleNamespace(text=" J'ai sauté.", no_speech_prob=0.1, avg_logprob=-0.2,
                                      words=[word(" J'", 0.5, 0.7), word("ai", 0.7, 0.9), word(" sauté.", 1.0, 1.5)]),
                types.SimpleNamespace(text=" Sous-titres réalisés par la communauté", no_speech_prob=0.2,
                                      avg_logprob=-0.3, words=[word(" Sous-titres", 2.0, 2.5)]),
            ]
            return iter(segs), types.SimpleNamespace(language="fr")

    monkeypatch.delenv("CLIPPER_MOCK_WORDS", raising=False)
    monkeypatch.setitem(transcribe._MODELS, "small", FakeModel())
    monkeypatch.setitem(sys.modules, "faster_whisper",
                        types.SimpleNamespace(WhisperModel=lambda *a, **k: FakeModel()))
    words, lang = transcribe.transcribe(wav_src, 1.0, 3.0, model_name="small", workdir=tmp_path)
    assert seen["type"] == "ndarray" and seen["dtype"] == "float32" and seen["n"] == 48000
    assert seen["kw"]["word_timestamps"] is True
    assert lang == "fr" and [w["w"] for w in words] == ["J'AI", "SAUTÉ"]
    assert abs(words[0]["s"] - 1.5) < 1e-6 and words[1]["eos"]


def test_github_models_fall_back_to_text_and_explain_empty_answers(monkeypatch):
    import types

    cfg = load_config()
    calls = []

    def fake_get(url, **kw):
        return types.SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, text="[]",
                                     json=lambda: [{"id": "openai/gpt-4o-mini"}, {"id": "openai/other"}])

    def fake_post(url, json=None, **kw):
        has_image = isinstance(json["messages"][1]["content"], list)
        calls.append((json["model"], has_image))
        body = {"choices": [{"message": {"content": None if has_image else '{"score": 7}'}}]}
        return types.SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                                     json=lambda: body, text=str(body))

    monkeypatch.setenv("GITHUB_TOKEN", "x" * 10)
    monkeypatch.setattr(ai.requests, "get", fake_get)
    monkeypatch.setattr(ai.requests, "post", fake_post)
    monkeypatch.setitem(ai._state, "github_model", None)
    monkeypatch.setitem(ai._state, "github_catalog", None)
    out = ai._github(cfg, "système", "texte", b"\xff\xd8image")
    assert out == '{"score": 7}'
    assert calls == [("openai/gpt-4o-mini", True), ("openai/gpt-4o-mini", False)]


def test_github_models_closed_service_is_dropped_once(monkeypatch):
    import types

    cfg = load_config()
    calls = []

    def stub(*a, **k):
        calls.append(1)
        return types.SimpleNamespace(status_code=200, headers={"content-type": "text/plain"}, text="OK\r\n",
                                     json=lambda: (_ for _ in ()).throw(ValueError("pas du JSON")))

    monkeypatch.setenv("GITHUB_TOKEN", "x" * 10)
    monkeypatch.setattr(ai.requests, "get", stub)
    monkeypatch.setattr(ai.requests, "post", stub)
    monkeypatch.setitem(ai._state, "github_model", None)
    monkeypatch.setitem(ai._state, "github_catalog", None)
    monkeypatch.setitem(ai._state, "off", set())
    assert ai._github(cfg, "s", "t", b"img") is None
    assert "github" in ai._state["off"] and len(calls) == 2   # catalogue + un seul appel
