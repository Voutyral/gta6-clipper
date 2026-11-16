"""Transcription mot à mot avec Whisper (faster-whisper), sur processeur."""
from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

from .captions import normalize_words
from .util import log, run

_MODELS: dict = {}

# Phrases que Whisper invente sur de la musique ou du silence.
HALLUCINATIONS = (
    "sous-titres", "sous titres", "sous-titrage", "amara.org", "merci d'avoir regardé",
    "thanks for watching", "abonnez-vous", "subscribe", "radio-canada",
)

_MOCK = ("Attends regarde ça. Il arrive à fond sur le pont. Non mais c'est pas possible ! "
         "Il a vraiment sauté avec la voiture. Franchement ce jeu est complètement fou. "
         "Regarde la police derrière lui. Oh là là il va jamais s'en sortir. Incroyable !").split()


def _mock_words(dur: float) -> list[dict]:
    """Paroles factices pour les essais sans modèle de transcription."""
    words, t, i = [], 0.6, 0
    while t < dur - 0.6:
        raw = _MOCK[i % len(_MOCK)]
        d = 0.16 + 0.045 * len(raw.strip(".!?"))
        words.append({"raw": raw, "s": t, "e": t + d, "p": 1.0})
        t += d + (0.55 if raw[-1] in ".!?" else 0.05)
        i += 1
    return words


def transcribe(src: str | Path, ss: float, dur: float, *, model_name: str = "small",
               language: str | None = None, workdir: str | Path = ".") -> tuple[list[dict], str]:
    """Mots horodatés en temps du fichier source (ss inclus) et langue détectée."""
    if os.environ.get("CLIPPER_MOCK_WORDS"):
        words, lang = _mock_words(dur), language or "fr"
    else:
        words, lang = _whisper(src, ss, dur, model_name, language, Path(workdir))
    out = normalize_words(words, dur)
    for w in out:
        w["s"] += ss
        w["e"] += ss
    return out, lang


def _whisper(src, ss: float, dur: float, model_name: str, language: str | None,
             workdir: Path) -> tuple[list[dict], str]:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # paquet absent : on continue sans sous-titres
        log(f"Sous-titres indisponibles (faster-whisper absent : {exc})")
        return [], ""
    wav = workdir / f"asr_{int(ss * 1000)}_{int(dur * 1000)}.wav"
    try:
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}",
             "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)], quiet=True)
        # Le son est lu ici puis donné à Whisper en tableau : le décodeur intégré
        # de faster-whisper (PyAV) casse selon les versions installées.
        with wave.open(str(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if audio.size < 1600:
            return [], ""
        if model_name not in _MODELS:
            log(f"Chargement du modèle Whisper « {model_name} »…")
            _MODELS[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8",
                                               cpu_threads=os.cpu_count() or 2)
        segments, info = _MODELS[model_name].transcribe(
            audio, language=language, word_timestamps=True, vad_filter=True, beam_size=5,
            condition_on_previous_text=False)
        words: list[dict] = []
        for seg in segments:
            text = (seg.text or "").strip().lower()
            if any(h in text for h in HALLUCINATIONS):
                continue
            if getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -1.0:
                continue
            for w in seg.words or []:
                tok = w.word or ""
                if not tok.strip():
                    continue
                # « J' » + « ai » : Whisper sépare après l'apostrophe, on recolle.
                if words and not tok.startswith(" ") and words[-1]["raw"][-1:] in "'’-":
                    words[-1]["raw"] += tok.strip()
                    words[-1]["e"] = float(w.end)
                else:
                    words.append({"raw": tok.strip(), "s": float(w.start), "e": float(w.end),
                                  "p": float(getattr(w, "probability", 1.0) or 1.0)})
        lang = getattr(info, "language", "") or ""
        log(f"Transcription : {len(words)} mots, langue « {lang or '?'} ».")
        return words, lang
    except Exception as exc:
        log(f"Transcription échouée, clip rendu sans sous-titres : {exc}")
        return [], ""
    finally:
        wav.unlink(missing_ok=True)


def words_to_lines(words: list[dict], origin: float, per_line: int = 12, limit: int = 1400) -> str:
    """Transcription compacte « [secondes] texte » pour les prompts."""
    lines = []
    for i in range(0, len(words), per_line):
        chunk = words[i:i + per_line]
        lines.append(f"[{chunk[0]['s'] - origin:.1f}] " + " ".join(w["raw"] for w in chunk))
    text = "\n".join(lines)
    return text[:limit]
