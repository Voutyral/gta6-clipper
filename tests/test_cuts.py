"""Coupes dynamiques : morceaux gardés, calage sur les mots, garde-fous, montage réel."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from clipper import cuts
from clipper.config import load_config
from clipper.pipeline import hashtags_for
from clipper.render import Plan, render
from clipper.util import probe_media

ED = {"jump_cuts": True, "max_cuts": 6, "min_after_cuts_s": 10, "min_keep_ratio": 0.55}


def words_every(step: float, start: float, end: float) -> list[dict]:
    out, t, i = [], start, 0
    while t + 0.25 <= end:
        out.append({"raw": f"mot{i}", "w": f"MOT{i}", "s": t, "e": t + 0.25, "p": 1.0, "eos": False})
        t += step
        i += 1
    return out


def test_kept_pieces_and_timeline():
    segs = cuts.keep_segments(10.0, 40.0, [(15.0, 18.0), (30.0, 31.0)])
    assert segs == [(10.0, 15.0), (18.0, 30.0), (31.0, 40.0)]
    assert cuts.remap(10.0, segs) == 0.0
    assert cuts.remap(18.5, segs) == pytest.approx(5.5)
    assert cuts.remap(16.0, segs) is None              # dans une coupe
    # Un morceau trop court entre deux coupes disparaît avec elles.
    assert cuts.keep_segments(0.0, 20.0, [(5.0, 8.0), (8.5, 10.0)]) == [(0.0, 5.0), (10.0, 20.0)]


def test_ai_cuts_never_split_a_word():
    words = [{"s": 4.8, "e": 5.3}, {"s": 9.9, "e": 10.4}]
    got = cuts.from_ai([[5.0, 10.0]], 0.0, 0.0, 30.0, words, ED)
    assert got == [(5.3, 9.9)]


def test_ai_cuts_are_in_extract_time_and_clipped_to_the_chosen_window():
    got = cuts.from_ai([[2, 4], [40, 45]], 100.0, 101.0, 130.0, [], ED)   # extrait à 100 s
    assert got == [(102.0, 104.0)]


def test_cutting_too_much_is_refused():
    assert cuts.from_ai([[1, 18]], 0.0, 0.0, 24.0, [], ED) == []          # resterait 7 s
    assert cuts.from_ai([[1, 2]], 0.0, 0.0, 24.0, [], dict(ED, jump_cuts=False)) == []
    assert cuts.from_ai("n'importe quoi", 0.0, 0.0, 24.0, [], ED) == []
    assert cuts.from_ai([[3, 3.2], ["a", 2]], 0.0, 0.0, 24.0, [], ED) == []   # trop court, illisible


def test_hashtags_follow_the_game_on_screen():
    cfg = load_config()
    assert hashtags_for("GTA 5", cfg)[:2] == ["#GTA5", "#GTAV"]
    assert hashtags_for("GTA Online", cfg)[0] == "#GTA5"
    assert hashtags_for("Grand Theft Auto VI", cfg)[0] == "#GTA6"
    assert hashtags_for("Fortnite", cfg) == ["#Fortnite", "#Gaming", "#Shorts"]
    assert hashtags_for("", cfg) == cfg["metadata"]["hashtags"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
def test_render_with_cuts_is_shorter_and_keeps_captions_in_order(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=14",
                    "-f", "lavfi", "-i", "sine=f=440:d=14", "-shortest", "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", str(src)], check=True)
    cfg = load_config()
    cfg["edit"].update(music="off", sfx=False, teaser="off", punch=False, x264_preset="ultrafast")
    words = words_every(0.5, 0.6, 13.2)
    plan = Plan(src=src, start=0.5, end=13.5, words=words, hook="Test", credit="@x",
                cuts=[(4.0, 6.0), (9.0, 10.0)])
    facts = render(plan, tmp_path / "out.mp4", cfg, tmp_path / "w")
    assert facts["cuts"] == 2 and facts["cut_s"] == pytest.approx(3.0)
    assert probe_media(tmp_path / "out.mp4")["duration"] == pytest.approx(10.0, abs=0.25)
    cut_out = [w for w in words if 4.0 <= (w["s"] + w["e"]) / 2 < 6.0 or 9.0 <= (w["s"] + w["e"]) / 2 < 10.0]
    assert facts["captions"] == len(words) - len(cut_out)
