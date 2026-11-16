"""Bruitages synthétisés sur place : sons originaux, donc aucun droit à gérer."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000


def _write(path: Path, left: np.ndarray, right: np.ndarray) -> None:
    peak = max(float(np.abs(left).max()), float(np.abs(right).max()), 1e-9)
    stereo = np.stack([left, right], axis=1) / peak * 0.89
    pcm = (stereo * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def whoosh(path: Path, dur: float = 0.45) -> None:
    """Souffle filtré qui balaie de gauche à droite : transition entre deux plans."""
    rng = np.random.default_rng(7)
    n = int(SR * dur)
    t = np.linspace(0.0, 1.0, n)
    noise = rng.standard_normal(n)
    cutoff = 300 + 6500 * np.sin(np.pi * t) ** 2
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.zeros(n)
    prev = 0.0
    for i in range(n):
        prev = (1 - a[i]) * noise[i] + a[i] * prev
        y[i] = prev
    y *= np.sin(np.pi * t) ** 2 * (0.35 + 0.65 * t)
    _write(path, y * np.sqrt(1 - t), y * np.sqrt(t))


def impact(path: Path, dur: float = 0.7) -> None:
    """Coup sourd (basse qui descend) avec un claquement bref : souligne un impact."""
    rng = np.random.default_rng(11)
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 38 + 95 * np.exp(-t * 14)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 5.5)
    click = rng.standard_normal(n) * np.exp(-t * 90) * 0.45
    y = np.tanh(1.8 * (body + click))
    y *= np.minimum(1.0, (n - np.arange(n)) / (0.02 * SR))
    _write(path, y, y)


def ensure(workdir: Path) -> dict[str, Path]:
    """Crée les bruitages une fois par dossier de travail."""
    files = {"whoosh": workdir / "sfx_whoosh.wav", "impact": workdir / "sfx_impact.wav"}
    if not files["whoosh"].exists():
        whoosh(files["whoosh"])
    if not files["impact"].exists():
        impact(files["impact"])
    return files
