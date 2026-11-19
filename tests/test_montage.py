"""Technique de montage : sous-titres coupés au bon endroit, temps morts resserrés, début noir retiré."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone

import numpy as np
import pytest

from clipper import ai, captions, cuts, detect, hunt, sources
from clipper.config import load_config
from clipper.render import Plan, render
from clipper.util import probe_media

ED = load_config()["edit"]


def said(text: str, t0: float = 0.0, step: float = 0.32) -> list[dict]:
    out, t = [], t0
    for tok in text.split():
        out.append({"raw": tok, "s": t, "e": t + 0.26})
        t += step
    return captions.normalize_words(out, 120.0)


def shown(text: str) -> list[str]:
    return [" ".join(w["w"] for w in g) for g in captions.group_words(said(text), 3, 13)]


def test_captions_never_end_on_a_small_word():
    assert shown("Run out the door right now guys") == ["RUN OUT", "THE DOOR", "RIGHT NOW", "GUYS"]
    assert shown("Somebody hold the door") == ["SOMEBODY HOLD", "THE DOOR"]
    assert shown("Mais c'est incroyable ce truc") == ["MAIS", "C'EST INCROYABLE", "CE TRUC"]


def test_captions_break_at_commas_and_sentence_ends():
    assert shown("Le pont tombe, le pont tombe !") == ["LE PONT TOMBE", "LE PONT TOMBE"]
    assert shown("Clear okay? Oh my god!") == ["CLEAR OKAY?", "OH MY GOD!"]


def _profiles(dur: float, words: list[dict], *, loud=(), moving=(), hop=0.05, fps=10.0):
    """Son et mouvement factices : voix à -20 dB, silences à -42 dB, image calme sauf `moving`."""
    db = np.full(int(dur / hop) + 1, -42.0, dtype=np.float32)
    for w in words:
        db[int(w["s"] / hop):int(w["e"] / hop) + 1] = -20.0
    for a, b in loud:
        db[int(a / hop):int(b / hop) + 1] = -16.0
    motion = np.full(int(dur * fps) + 1, 1.0, dtype=np.float32)
    for a, b in moving:
        motion[int(a * fps):int(b * fps) + 1] = 14.0
    return db, motion


def _speech_with_gaps() -> list[dict]:
    # Quatre phrases : 1 s de silence au début, puis trois silences de 3 à 4 s (vers 3 s, 8 s et 14 s).
    return (said("on y va les gars regarde bien", 1.0) + said("il arrive par la droite maintenant", 6.0)
            + said("et là il saute du pont", 12.0) + said("non mais c'est pas possible", 18.0))


def test_dead_air_is_cut_only_when_quiet_and_still():
    words = _speech_with_gaps()
    db, motion = _profiles(24.0, words, loud=[(10.5, 11.5)], moving=[(16.5, 17.5)])
    gaps = cuts.dead_gaps(words, 0.0, 24.0, db, motion, ed=ED)
    # Le silence du début et celui d'après la première phrase sont raccourcis (avec un souffle de
    # chaque côté de la coupe) ; le suivant a du son (une explosion), le dernier de l'action à
    # l'image : on n'y touche pas.
    assert gaps == [(0.0, 0.88), (3.305, 5.875)]


def test_silence_before_the_first_word_is_trimmed():
    words = said("attention il arrive à fond", 2.5)
    db, motion = _profiles(12.0, words)
    gaps = cuts.dead_gaps(words, 0.0, 12.0, db, motion, ed=ED)
    assert gaps and gaps[0][0] == 0.0 and 2.2 < gaps[0][1] < 2.5


def test_tighten_keeps_ai_cuts_respects_budget_and_can_be_switched_off(monkeypatch):
    words = _speech_with_gaps()
    db, motion = _profiles(24.0, words, loud=[(10.5, 11.5)], moving=[(16.5, 17.5)])
    monkeypatch.setattr(detect, "energy_profile", lambda *a, **k: db)
    monkeypatch.setattr(detect, "motion_profile", lambda *a, **k: motion)
    out = cuts.tighten("x.mp4", 0.0, 24.0, words, ED, fixed=[(20.5, 21.5)])
    assert out == [(0.0, 0.88), (3.305, 5.875), (20.5, 21.5)]
    stingy = dict(ED, tighten_max_share=0.02)          # 0,5 s au plus : rien ne passe
    assert cuts.tighten("x.mp4", 0.0, 24.0, words, stingy, fixed=[(20.5, 21.5)]) == [(20.5, 21.5)]
    assert not cuts.tighten_on(ED, {"tighten": "off"}) and cuts.tighten_on(ED, {})
    assert not cuts.tighten_on(dict(ED, tighten=False), None)


def test_hook_lines_are_balanced_and_never_end_on_a_small_word():
    assert captions.balanced("GOOD NEWS AND BAD NEWS", 18) == ["GOOD NEWS", "AND BAD NEWS"]
    assert captions.balanced("INTO THE SUNSET I DIE", 18) == ["INTO THE SUNSET", "I DIE"]
    assert captions.balanced("IL SAUTE  DU (PONT)", 18) == ["IL SAUTE DU (PONT)"]


def test_too_many_ai_cuts_keep_the_longest_instead_of_none():
    # 4 coupes de l'IA sur 30 s : toutes ensemble, il resterait 14 s (moins de 55 %) ; on garde
    # les plus longues tant que le clip garde au moins 16,5 s.
    got = cuts.from_ai([[2, 5], [8, 14], [17, 20], [23, 26]], 0.0, 0.0, 30.0, [], ED)
    assert got == [(2.0, 5.0), (8.0, 14.0), (17.0, 20.0)]


def test_ai_prompt_still_formats_and_asks_for_the_new_fields():
    text = ai.EVAL_SYSTEM.format(game="GTA 6", min_s=15, max_s=45, lang="fr")
    assert "première seconde" in text and "Barème" in text
    assert "captions_on_screen" not in text           # plus de retrait des sous-titres sur l'avis de l'IA


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
def test_black_start_and_motion_are_measured(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=1",
                    "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
                    str(src)], check=True)
    assert detect.black_lead(src, 0.0) == pytest.approx(1.0, abs=0.1)
    assert detect.black_lead(src, 1.5) == 0.0
    motion = detect.motion_profile(src, 0.0, 4.0)
    assert len(motion) >= 35 and motion[2:8].max() < 0.5 and motion[15:].mean() > 1.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
def test_split_render_with_sharper_webcam_and_clearer_voice(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=f=300:d=8", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", str(src)], check=True)
    cfg = load_config()
    cfg["edit"].update(music="off", sfx=False, teaser="off", punch=False, x264_preset="ultrafast")
    plan = Plan(src=src, start=0.5, end=7.5, words=said("regarde ça il arrive", 1.0), hook="Test",
                credit="@x", preset={"facecam": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3}})
    facts = render(plan, tmp_path / "out.mp4", cfg, tmp_path / "w")
    info = probe_media(tmp_path / "out.mp4")
    assert facts["layout"] == "split" and not facts["fallback"]
    assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"]


def test_daily_cap_stops_ordinary_clips_but_not_the_campaign(monkeypatch, tmp_path):
    cfg = load_config()
    cfg["hunt"]["max_per_day"] = 30
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen = {f"twitch:old{i}": {"d": today, "s": f"s{i}", "ok": True} for i in range(30)}
    (tmp_path / "seen.json").write_text(json.dumps(seen))
    monkeypatch.setattr(hunt, "SEEN", tmp_path / "seen.json")
    clip = sources.Found(platform="twitch", id="new1", url="https://www.twitch.tv/bob/clip/new1", title="lol",
                         views=5000, duration=30, created_at=datetime.now(timezone.utc), streamer="bob",
                         streamer_name="Bob", language="en", game="Grand Theft Auto VI")
    monkeypatch.setattr(hunt, "discover", lambda c, s: ([clip], []))
    tried: list = []
    monkeypatch.setattr(hunt, "process_request", lambda req, *a, **k: tried.append(req.url) or [])
    for name in ("step_summary", "cleanup_releases", "open_issue"):
        monkeypatch.setattr(hunt.deliver, name, lambda *a, **k: None)
    monkeypatch.setattr(hunt.deliver, "pages_url", lambda: "")
    hunt.run_hunt(cfg, [], tmp_path, tmp_path)
    assert tried == []                                   # 30 clips déjà montés aujourd'hui : plafond atteint
    seen.pop("twitch:old0")
    (tmp_path / "seen.json").write_text(json.dumps(seen))
    hunt.run_hunt(cfg, [], tmp_path, tmp_path)
    assert tried == ["https://www.twitch.tv/bob/clip/new1"]
    # Les clips d'un streamer en campagne ne comptent pas dans le plafond des autres.
    dom = {"name": "Dom", "kick": "dasgasd3m", "campaign": {"name": "C"}}
    paid = {f"kick:{i}": {"d": today, "s": "dasgasd3m", "ok": True} for i in range(40)}
    assert hunt.made_today_total(paid, [dom]) == 0 and hunt.made_today_total(paid, []) == 40


def test_nudity_is_flagged_not_blocked_except_for_campaigns_and_hate_always_blocks(tmp_path):
    from clipper import verify as qc
    assert "explicite" in qc.RISK_NOTES and "explicite" not in qc.RISK_LABELS
    assert "haine" in qc.RISK_LABELS
    text = ai.EVAL_SYSTEM.format(game="GTA 6", min_s=15, max_s=45, lang="fr")
    assert '"haine"' in text and "pas les simples gros mots" in text and "sorti le 19 novembre 2026" in text


def test_gemini_moves_to_the_next_model_when_a_daily_quota_is_spent(monkeypatch):
    import requests as rq
    ai._state.update(gemini_model="gemini-3-flash-preview", gemini_catalog=None, gemini_dry=set())
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body
            self.text = json.dumps(body)

        def json(self):
            return self._b

        def raise_for_status(self):
            pass

    catalog = {"models": [{"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                          {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
                          {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]}
    asked: list[str] = []

    def post(url, **k):
        model = url.split("/models/")[1].split(":")[0]
        asked.append(model)
        if model in ("gemini-3-flash-preview", "gemini-3.8-flash"):
            return R(429, {"error": {"code": 429, "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
        return R(200, {"candidates": [{"content": {"parts": [{"text": "{\"score\": 7}"}]}}]})

    monkeypatch.setattr(rq, "get", lambda *a, **k: R(200, catalog))
    monkeypatch.setattr(rq, "post", post)
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    cfg = load_config()
    cfg["ai"]["gemini_models"] = ["gemini-3-flash-preview", "gemini-3.8-flash"]
    assert ai._gemini(cfg, [{"text": "x"}], True) == "{\"score\": 7}"
    assert asked == ["gemini-3-flash-preview", "gemini-3.8-flash", "gemini-3.5-flash-lite"]   # pas de modèle vocal
    asked.clear()
    assert ai._gemini(cfg, [{"text": "x"}], True) and asked == ["gemini-3.5-flash-lite"]   # les épuisés sont sautés
    ai._state.update(gemini_model=None, gemini_catalog=None, gemini_dry=set())


def test_unknown_webcam_keeps_the_whole_game_picture():
    from clipper.pipeline import Candidate, build_plan
    from clipper.render import layout_chain
    from clipper.request import ClipRequest
    ed = load_config()["edit"]
    from pathlib import Path
    cand = Candidate(Path("a.mp4"), 100.0, 140.0, None, 1.0, 0.0, locked=True)
    info = {"title": "Session GTA 6", "webpage_url": "https://x.tv/v", "channel": "Bob"}
    blind = build_plan(cand, None, [], ClipRequest(url="https://x.tv/v"), info, load_config(), {}, "@Bob", 0)
    assert "plein_cadre" in blind.flags                                          # l'IA n'a pas répondu
    seen = build_plan(cand, {"score": 7}, [], ClipRequest(url="https://x.tv/v"), info, load_config(), {}, "@Bob", 0)
    assert "plein_cadre" not in seen.flags                                       # l'IA a regardé : pas de webcam
    # Dialogues de GTA 6 sous-titrés à l'écran : les paroles du streamer restent sous-titrées.
    talk = build_plan(cand, {"score": 7, "captions_on_screen": True}, [], ClipRequest(url="https://x.tv/v"), info,
                      load_config(), {}, "@Bob", 0)
    assert "nosub" not in talk.flags
    assert "scale=1620:-2" in layout_chain("0:v", "v0", "blur", ed, {}, "60", "0")       # zoom 1.5 : côtés rognés
    assert "scale=1080:-2" in layout_chain("0:v", "v0", "blur", dict(ed, zoom=1.0), {}, "60", "0")


def test_campaign_cuts_never_go_below_the_brief_minimum():
    from pathlib import Path

    from clipper import campaign
    from clipper.pipeline import Candidate, build_plan
    from clipper.request import ClipRequest
    dom = {"name": "Dom", "kick": "dasgasd3m", "tighten": "off", "detection": {"clip_min_s": 20, "clip_max_s": 60},
           "campaign": {"name": "C", "opens": "2026-11-19T09:00:00-08:00", "closes": "2026-12-19T23:59:00-08:00"}}
    cfg = campaign.tuned(load_config(), dom)
    cand = Candidate(Path("a.mp4"), 0.0, 30.0, None, 1.0, 0.0, locked=True)
    info = {"title": "GTA 6", "webpage_url": "https://kick.com/dasgasd3m", "channel": "Dom"}
    ev = {"score": 8, "cuts": [[4, 9], [12, 18]]}          # 11 s retirées : il resterait 19 s
    plan = build_plan(cand, ev, [], ClipRequest(url="https://kick.com/x"), info, cfg, dom, "dasgasd3m on Kick", 0)
    kept = 30.0 - sum(b - a for a, b in plan.cuts)
    assert kept >= 20.0 and plan.cuts == [(12.0, 18.0)]    # la plus longue coupe seulement
    free = build_plan(cand, ev, [], ClipRequest(url="https://kick.com/x"), info, load_config(), {}, "@x", 0)
    assert len(free.cuts) == 2                            # hors campagne : 19 s restent permises


def test_ai_webcam_frame_is_checked_and_recentred_on_the_face(monkeypatch):
    # Faux détecteur : renvoie un visage à une position donnée (en fractions de la zone fouillée).
    spot = {"face": None}
    monkeypatch.setattr(detect, "_gray_region", lambda *a, **k: np.zeros((360, 640), dtype=np.uint8))

    class Finder:
        def detectMultiScale(self, img, *a, **k):
            if spot["face"] is None:
                return []
            fx, fy = spot["face"]
            return [(int(fx * 640) - 30, int(fy * 360) - 30, 60, 60)]

    monkeypatch.setattr(detect, "_face_finders", lambda: [("haarcascade_frontalface_default.xml", Finder())])
    pytest.importorskip("cv2")
    box = {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.36}
    # Zone fouillée = cadre élargi de moitié : x de 0,55 à 1, y de 0 à 0,54.
    spot["face"] = (0.6, 0.3)          # visage au milieu du cadre de l'IA (x 0,82, y 0,16)
    assert detect.fit_webcam("x.mp4", 0.0, 20.0, box) == box
    spot["face"] = (0.1, 0.3)          # visage à gauche, hors du cadre (x 0,60) : cadre décalé
    moved = detect.fit_webcam("x.mp4", 0.0, 20.0, box)
    assert moved["x"] < 0.6 < moved["x"] + moved["w"] and moved["w"] >= 0.3 and moved["h"] >= 0.36
    spot["face"] = None                # aucun visage : cadre douteux (le chat, un mur)
    assert detect.fit_webcam("x.mp4", 0.0, 20.0, box) is None


def test_hate_slurs_are_masked_on_screen_and_refuse_the_clip():
    for bad in ("nigga", "Niggas,", "NIGGER", "faggot", "pédé", "PÉDÉS !", "retarded", "chinks"):
        assert captions.is_slur(bad), bad
    for ok in ("night", "Niger", "Nigeria", "bigger", "negroni", "spice", "pédale", "fuck", "putain"):
        assert not captions.is_slur(ok), ok
    shown_words = captions.normalize_words([{"raw": "nigga", "s": 0.0, "e": 0.3}, {"raw": "what", "s": 0.4, "e": 0.6}], 5)
    assert [w["w"] for w in shown_words] == ["N****", "WHAT"]
    from pathlib import Path

    from clipper.pipeline import Candidate, build_plan
    from clipper.request import ClipRequest
    words = [{"raw": "yo", "s": 2.0, "e": 2.3}, {"raw": "nigga", "s": 2.4, "e": 2.8}, {"raw": "look", "s": 3.0, "e": 3.3}]
    cand = Candidate(Path("a.mp4"), 0.0, 25.0, None, 1.0, 0.0, locked=True, words=words)
    info = {"title": "GTA 6", "webpage_url": "https://kick.com/x", "channel": "Dom"}
    plan = build_plan(cand, {"score": 8, "risks": []}, [], ClipRequest(url="https://kick.com/x"), info,
                      load_config(), {}, "@x", 0)
    assert "haine" in plan.risks
    clean = Candidate(Path("a.mp4"), 0.0, 25.0, None, 1.0, 0.0, locked=True, words=words[:1])
    assert "haine" not in build_plan(clean, {"score": 8}, [], ClipRequest(url="https://kick.com/x"), info,
                                     load_config(), {}, "@x", 0).risks


def test_swear_words_are_masked_on_screen_and_in_the_title():
    for bad in ("fucking", "SHIT!", "fuckin'", "Motherfucker", "bullshit", "bitch", "putain", "Enculé", "merde"):
        assert captions.is_swear(bad), bad
    for ok in ("bite", "class", "assassin", "shoot", "duck", "Dom", "shift", "con", "chat"):
        assert not captions.is_swear(ok), ok
    assert captions.censor_text("OH SHIT, he said fuck-up!") == "OH S***, he said f***-up!"
    cfg = load_config()
    words = said("fuck that guy")
    ass = captions.build_ass(words, hook="OH SHIT", context="Putain de voiture", credit="", total=3.0, cfg=cfg)
    assert "F***" in ass and "S***" in ass and "P*****" in ass
    assert "FUCK" not in ass and "SHIT" not in ass and "Putain" not in ass
    assert [w["w"] for w in words][0] == "FUCK"                     # mots d'origine intacts (coupes, insultes)
    off = dict(cfg, captions=dict(cfg["captions"], censor=False))
    raw = captions.build_ass(words, hook="OH SHIT", context="", credit="", total=3.0, cfg=off)
    assert "FUCK" in raw and "SHIT" in raw
    from pathlib import Path

    from clipper.pipeline import Candidate, build_plan
    from clipper.request import ClipRequest
    cand = Candidate(Path("a.mp4"), 0.0, 25.0, None, 1.0, 0.0, locked=True)
    info = {"title": "GTA 6", "webpage_url": "https://kick.com/x", "channel": "Dom"}
    ev = {"score": 8, "risks": [], "title": "Dom says fuck that guy", "description": "He yells shit at a cop."}
    plan = build_plan(cand, ev, [], ClipRequest(url="https://kick.com/x"), info, cfg, {}, "@x", 0)
    assert plan.title == "Dom says f*** that guy" and "s*** at a cop" in plan.description
