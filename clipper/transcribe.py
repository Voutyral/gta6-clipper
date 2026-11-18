"""Transcription mot à mot, sur processeur.

Moteur par défaut : Parakeet TDT 0.6B v3 (NVIDIA), via sherpa-onnx. Sur un vrai clip français
il a écrit « l'avion de chasse frérot il a grossi » et « dinguerie » là où Whisper small
écrivait « la viande chasse » et « dingue rie ». Il est aussi rapide (3 s pour 28 s de son).
Whisper (faster-whisper) reste disponible en secours ou via config.yaml.
"""
from __future__ import annotations

import os
import re
import tarfile
import wave
from pathlib import Path

import numpy as np
import requests

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
               language: str | None = None, workdir: str | Path = ".", engine: str = "whisper",
               vocab: list[str] | None = None, cfg: dict | None = None) -> tuple[list[dict], str]:
    """Mots horodatés en temps du fichier source (ss inclus) et langue détectée."""
    if os.environ.get("CLIPPER_MOCK_WORDS"):
        words, lang = _mock_words(dur), language or "fr"
    elif engine == "parakeet":
        words, lang = _parakeet(src, ss, dur, Path(workdir), cfg or {})
        if words is None:   # moteur indisponible : Whisper prend le relais
            words, lang = _whisper(src, ss, dur, model_name, language, Path(workdir), vocab)
    else:
        words, lang = _whisper(src, ss, dur, model_name, language, Path(workdir), vocab)
    out = normalize_words(words, dur)
    for w in out:
        w["s"] += ss
        w["e"] += ss
    return out, lang


def _whisper(src, ss: float, dur: float, model_name: str, language: str | None,
             workdir: Path, vocab: list[str] | None = None) -> tuple[list[dict], str]:
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
        # Une courte amorce oriente Whisper vers les noms et l'argot du fichier vocabulaire.
        prompt = ("Vocabulaire : " + ", ".join(vocab[:40]) + ".") if vocab else None
        segments, info = _MODELS[model_name].transcribe(
            audio, language=language, word_timestamps=True, vad_filter=True, beam_size=5,
            condition_on_previous_text=False, initial_prompt=prompt)
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


# ---------------------------------------------------------------- Parakeet (sherpa-onnx)

PARAKEET = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
PARAKEET_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{PARAKEET}.tar.bz2"
CACHE = Path(os.environ.get("CLIPPER_CACHE", Path.home() / ".cache" / "clipper"))


def _parakeet_dir() -> Path | None:
    """Modèle Parakeet en cache, téléchargé la première fois (environ 650 Mo, depuis GitHub)."""
    target = CACHE / PARAKEET
    if (target / "tokens.txt").exists():
        return target
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        archive = CACHE / f"{PARAKEET}.tar.bz2"
        log("Téléchargement du modèle de sous-titres Parakeet (une seule fois)…")
        with requests.get(PARAKEET_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(archive, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(CACHE, filter="data")
        archive.unlink(missing_ok=True)
        return target if (target / "tokens.txt").exists() else None
    except Exception as exc:
        log(f"Modèle Parakeet indisponible ({exc}) : Whisper prend le relais.")
        return None


def tokens_to_words(tokens: list[str], starts: list[float], durations: list[float] | None = None) -> list[dict]:
    """Morceaux de mots (« ▁ch », « asse ») → mots entiers avec début et fin."""
    words: list[dict] = []
    for i, tok in enumerate(tokens):
        t = float(starts[i]) if i < len(starts) else (words[-1]["e"] if words else 0.0)
        d = float(durations[i]) if durations is not None and i < len(durations) else 0.08
        end = t + max(d, 0.08)
        piece = tok.replace("▁", " ")
        bare = piece.strip()
        if not bare:
            continue
        new_word = piece.startswith(" ") or not words
        if not new_word or (words and re.fullmatch(r"[?!.,;:…»)]+", bare)):
            words[-1]["raw"] += bare
            words[-1]["e"] = max(words[-1]["e"], end)
        else:
            words.append({"raw": bare, "s": t, "e": end, "p": 1.0})
    for a, b in zip(words, words[1:], strict=False):   # un mot finit au plus tard quand le suivant commence
        a["e"] = min(a["e"], b["s"]) if b["s"] > a["s"] else a["e"]
    for w in words:   # le dernier morceau avant un silence peut « durer » plusieurs secondes
        w["e"] = min(w["e"], w["s"] + 1.2)
    return words


_STOP = {
    "fr": {"je", "tu", "il", "on", "le", "la", "les", "de", "des", "est", "pas", "que", "qu'", "c'est", "mais",
           "et", "ça", "quoi", "un", "une", "en", "vrai", "oui", "non", "moi", "toi", "nous", "vous", "ce", "j'ai"},
    "en": {"the", "you", "i", "it", "is", "and", "to", "a", "what", "that", "this", "no", "yes", "oh", "my", "of"},
    "es": {"el", "la", "que", "de", "no", "es", "y", "qué", "pero", "lo", "por", "con", "una", "mi"},
    "de": {"der", "die", "das", "und", "ich", "du", "nicht", "ist", "was", "ja", "nein", "ein", "mit"},
    "it": {"il", "che", "di", "non", "è", "e", "ma", "cosa", "sono", "una", "io", "per"},
    "pt": {"o", "que", "não", "de", "é", "e", "um", "uma", "eu", "você", "isso", "mas", "com"},
}


def guess_language(words: list[dict]) -> str:
    """Langue probable d'après les petits mots les plus courants. Vide si incertain."""
    toks = [re.sub(r"[^\w']", "", w["raw"].lower()) for w in words]
    toks = [t for t in toks if t]
    if len(toks) < 4:
        return ""
    scores = {lang: sum(1 for t in toks if t in stop) / len(toks) for lang, stop in _STOP.items()}
    ranked = sorted(scores, key=scores.get, reverse=True)
    best, second = scores[ranked[0]], scores[ranked[1]]
    # Trop peu de petits mots, ou deux langues au coude à coude : on ne tranche pas (l'IA le fera).
    return ranked[0] if best >= 0.12 and best >= 1.5 * second else ""


def chunks(audio: np.ndarray, sr: int = 16000, max_s: float = 40.0, search_s: float = 8.0) -> list[tuple[int, int]]:
    """Découpe un long son en morceaux de `max_s` secondes au plus, en coupant dans un creux.

    Parakeet lit tout un morceau d'un coup : au delà d'une minute la mémoire grimpe vite.
    """
    n, max_n, hop = len(audio), int(max_s * sr), int(0.02 * sr)
    out, start = [], 0
    while n - start > max_n:
        lo, hi = start + int((max_s - search_s) * sr), start + max_n
        seg = audio[lo:hi]
        frames = seg[: (len(seg) // hop) * hop].reshape(-1, hop)
        cut = lo + int(np.argmin((frames ** 2).mean(axis=1))) * hop + hop // 2
        out.append((start, cut))
        start = cut
    out.append((start, n))
    return out


_PARAKEET_REC: dict = {}


def _parakeet(src, ss: float, dur: float, workdir: Path, cfg: dict) -> tuple[list[dict] | None, str]:
    try:
        import sherpa_onnx
    except Exception as exc:
        log(f"Parakeet indisponible (sherpa-onnx absent : {exc}).")
        return None, ""
    model = _parakeet_dir()
    if model is None:
        return None, ""
    wav = workdir / f"asr_{int(ss * 1000)}_{int(dur * 1000)}.wav"
    try:
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}",
             "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)], quiet=True)
        with wave.open(str(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if audio.size < 1600:
            return [], ""
        if "rec" not in _PARAKEET_REC:
            _PARAKEET_REC["rec"] = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(model / "encoder.int8.onnx"), decoder=str(model / "decoder.int8.onnx"),
                joiner=str(model / "joiner.int8.onnx"), tokens=str(model / "tokens.txt"),
                model_type="nemo_transducer", num_threads=os.cpu_count() or 2)
        rec = _PARAKEET_REC["rec"]
        words: list[dict] = []
        for a, b in chunks(audio):
            stream = rec.create_stream()
            stream.accept_waveform(16000, audio[a:b])
            rec.decode_stream(stream)
            res = stream.result
            durs = list(getattr(res, "durations", []) or []) or None
            for w in tokens_to_words(list(res.tokens), list(res.timestamps), durs):
                w["s"] += a / 16000
                w["e"] += a / 16000
                words.append(w)
        lang = guess_language(words)
        log(f"Transcription Parakeet : {len(words)} mots, langue probable « {lang or '?'} ».")
        return words, lang
    except Exception as exc:
        log(f"Parakeet a échoué ({exc}) : Whisper prend le relais.")
        return None, ""
    finally:
        wav.unlink(missing_ok=True)
