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
    assert "captions_on_screen" in text and "première seconde" in text and "Barème" in text


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
