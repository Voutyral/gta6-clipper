"""Sous-titres : assemblage des mots de Parakeet, langue, découpe, correction par l'IA, vocabulaire."""
from __future__ import annotations

import numpy as np

from clipper import transcribe, vocab, wordfix
from clipper.pipeline import Candidate, ai_fix_words
from clipper.request import ClipRequest


def _words(text: str, t0: float = 0.0, step: float = 0.3) -> list[dict]:
    out = []
    for i, w in enumerate(text.split()):
        out.append({"raw": w, "w": w.upper(), "s": t0 + i * step, "e": t0 + i * step + 0.25, "p": 1.0,
                    "eos": False})
    return out


def test_parakeet_pieces_become_words():
    toks = [" C", "'", "est", " quoi", " ça", "?", " ch", "asse", " fr", "é", "rot", "!"]
    starts = [0.0, 0.08, 0.1, 0.3, 0.5, 0.6, 1.0, 1.1, 1.3, 1.4, 1.5, 1.6]
    words = transcribe.tokens_to_words(toks, starts, [0.08] * len(toks))
    assert [w["raw"] for w in words] == ["C'est", "quoi", "ça?", "chasse", "frérot!"]
    assert words[3]["s"] == 1.0 and words[3]["e"] <= words[4]["s"]
    # Variante SentencePiece avec « ▁ » en début de mot.
    words = transcribe.tokens_to_words(["▁Oh", "▁la", "▁ding", "uer", "ie"], [0, 0.2, 0.4, 0.5, 0.6])
    assert [w["raw"] for w in words] == ["Oh", "la", "dinguerie"]


def test_a_word_never_lingers_through_a_long_silence():
    words = transcribe.tokens_to_words([" fini"], [0.8], [1.44 + 3.0])
    assert words[0]["e"] - words[0]["s"] <= 1.2


def test_language_guess_from_small_words():
    fr = _words("mais c'est quoi ça frérot il a grossi quand même je te jure")
    en = _words("oh my god what is that you can't land on it")
    assert transcribe.guess_language(fr) == "fr"
    assert transcribe.guess_language(en) == "en"
    assert transcribe.guess_language(_words("GTA")) == ""
    # Un streamer français qui lâche deux mots d'anglais reste français.
    assert transcribe.guess_language(_words("oh my god non mais t'es fou il est où le frère")) == "fr"
    assert transcribe.guess_language(_words("C'est fini. Mec est-ce qu'on lui donne ? En vrai on lui donne.")) == "fr"


def test_long_audio_is_cut_in_a_quiet_spot():
    sr = 16000
    rng = np.random.default_rng(0)
    audio = rng.normal(0, 0.3, sr * 100).astype(np.float32)
    audio[int(34.5 * sr):int(35.5 * sr)] = 0.0
    parts = transcribe.chunks(audio, sr)
    assert parts[0][0] == 0 and parts[-1][1] == len(audio)
    assert 34.4 <= parts[0][1] / sr <= 35.6
    assert all(b - a <= 40 * sr for a, b in parts)
    assert all(parts[i][1] == parts[i + 1][0] for i in range(len(parts) - 1))


def test_ai_correction_keeps_word_timing():
    heard = _words("mais la viande chasse frérot il est agro-ci quand même", t0=5.0)
    fixed, ratio, changed = wordfix.align(heard, "Mais l'avion de chasse frérot, il a grossi quand même !")
    texts = [w["w"] for w in fixed]
    assert texts == ["MAIS", "L'AVION", "DE", "CHASSE", "FRÉROT", "IL", "A", "GROSSI", "QUAND", "MÊME"]
    by = {w["w"]: w for w in fixed}
    assert by["CHASSE"]["s"] == heard[3]["s"]          # mot reconnu : temps d'origine
    assert heard[1]["s"] <= by["L'AVION"]["s"] < by["DE"]["s"] < by["CHASSE"]["s"]
    assert all(a["s"] <= b["s"] for a, b in zip(fixed, fixed[1:], strict=False))
    assert ratio > 0.5 and len(changed) >= 2
    assert ("la viande", "l'avion de") in changed
    assert "« la viande » → « l'avion de »" in wordfix.describe(changed)


def test_ai_correction_too_far_is_ignored():
    heard = _words("on y va les gars c'est parti")
    same, ratio, changed = wordfix.align(heard, "Abonnez-vous à la chaîne pour plus de vidéos de ce genre")
    assert same is heard and changed == [] and ratio < 0.45


def test_ai_words_far_from_any_speech_are_not_guessed():
    heard = _words("attends regarde", t0=0.0) + _words("oh la dinguerie", t0=12.0)
    fixed, _, _ = wordfix.align(heard, "attends regarde il arrive oh la dinguerie")
    assert [w["w"] for w in fixed] == ["ATTENDS", "REGARDE", "OH", "LA", "DINGUERIE"]


def test_ai_drops_words_it_does_not_hear():
    heard = _words("tu peux te poser dessus dessus merci")
    fixed, _, _ = wordfix.align(heard, "tu peux te poser dessus merci")
    assert [w["w"] for w in fixed] == ["TU", "PEUX", "TE", "POSER", "DESSUS", "MERCI"]


def test_vocabulary_file_and_names_first():
    base = vocab.load_vocab()
    assert "wesh" in base and "frérot" in base and "GTA 6" in base
    words = vocab.for_clip(base, "@MasterSnakou", "", "Zerator")
    assert words[:2] == ["MasterSnakou", "Zerator"]
    assert sum(1 for w in words if w.lower() == "mastersnakou") == 1


def _cand(text: str) -> Candidate:
    return Candidate(src="x.mp4", start=0.0, end=20.0, peak=None, score=1.0, origin=0.0,
                     words=_words(text), lang="")


def test_ai_fix_applies_transcript_and_language():
    c = _cand("oh la dingue rie wesh frère au")
    ok = ai_fix_words(c, {"transcript": "Oh la dinguerie wesh frérot", "spoken_lang": "fr"},
                      ClipRequest(url="u"), {"ai_fix": True})
    assert ok and c.lang == "fr"
    assert [w["w"] for w in c.words] == ["OH", "LA", "DINGUERIE", "WESH", "FRÉROT"]


def test_ai_fix_rejects_unwanted_language():
    c = _cand("oh my god what is that bro")
    req = ClipRequest(url="u")
    req.langs = ["fr"]
    assert not ai_fix_words(c, {"transcript": "oh my god what is that bro", "spoken_lang": "en"}, req, {})


def test_ai_language_counts_even_when_parakeet_heard_nothing():
    c = _cand("")
    c.words = []                      # langue que Parakeet ne sait pas écrire
    req = ClipRequest(url="u")
    req.langs = ["fr"]
    ev = {"transcript": "bhai yeh kya ho gaya delivery wala gussa", "spoken_lang": "hi"}
    assert not ai_fix_words(c, ev, req, {})


def test_a_few_english_words_do_not_reject_a_clip():
    c = _cand("oh my god")
    req = ClipRequest(url="u")
    req.langs = ["fr"]
    assert ai_fix_words(c, {"transcript": "oh my god", "spoken_lang": "en"}, req, {})


def test_ai_fix_can_be_turned_off():
    c = _cand("oh la dingue rie")
    before = [w["raw"] for w in c.words]
    ai_fix_words(c, {"transcript": "oh la dinguerie"}, ClipRequest(url="u"), {"ai_fix": False})
    assert [w["raw"] for w in c.words] == before


def test_correction_that_cannot_be_said_in_time_keeps_what_was_heard():
    heard = _words("attends il est tombé le pont", t0=0.0, step=0.15)   # débit rapide, pas de silence
    fixed, _, edits = wordfix.align(heard, "attends OK ouais hé derrière derrière derrière le pont tombé le pont")
    assert [w["w"] for w in fixed][:4] == ["ATTENDS", "IL", "EST", "TOMBÉ"]
    assert all(b["s"] - a["s"] >= 0.04 for a, b in zip(fixed, fixed[1:], strict=False))


def test_longer_correction_uses_the_following_pause():
    heard = _words("mon pac", t0=0.0, step=0.3) + _words("ouais", t0=2.0)
    fixed, _, _ = wordfix.align(heard, "mon pack de bières ouais")
    assert [w["w"] for w in fixed] == ["MON", "PACK", "DE", "BIÈRES", "OUAIS"]
    assert fixed[3]["e"] <= 2.0
    assert all(w["e"] - w["s"] >= 0.1 for w in fixed[1:4])     # lisibles, pas un éclair


def test_words_before_the_first_heard_word_need_room():
    heard = _words("mon pac ouais", t0=0.0)
    fixed, _, _ = wordfix.align(heard, "dommage que mon pac ouais")
    assert [w["w"] for w in fixed] == ["MON", "PAC", "OUAIS"]
