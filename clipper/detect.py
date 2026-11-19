"""Repérage des moments forts à partir du son.

Idée : un moment fort se voit moins au volume absolu qu'au contraste avec ce qui
l'entoure (un cri, un crash, un fou rire sortent du niveau habituel du stream).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Window:
    start: float
    end: float
    peak: float
    score: float


def energy_profile(path: str | Path, hop: float = 0.5, sr: int = 8000,
                   ss: float | None = None, dur: float | None = None) -> np.ndarray:
    """Niveau sonore (dB) par tranche de `hop` secondes, lu en flux pour rester léger."""
    n = max(1, int(sr * hop))
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = n * 2
    vals: list[np.ndarray] = []
    buf = b""
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(size * 64)
        if not chunk:
            break
        buf += chunk
        k = len(buf) // size
        if k:
            arr = np.frombuffer(buf[:k * size], dtype="<i2").astype(np.float32).reshape(k, n) / 32768.0
            vals.append(20 * np.log10(np.sqrt((arr ** 2).mean(axis=1) + 1e-10)))
            buf = buf[k * size:]
    proc.wait()
    if not vals:
        return np.zeros(0, dtype=np.float32)
    return np.maximum(np.concatenate(vals), -70.0)


def _rolling(x: np.ndarray, half: int, fn) -> np.ndarray:
    if len(x) <= 2 * half + 1:
        return np.full_like(x, fn(x))
    pad = np.pad(x, half, mode="reflect")
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * half + 1)
    return fn(win, axis=1)


def excitement(db: np.ndarray, hop: float, context_s: float = 45.0) -> np.ndarray:
    """Écart positif au niveau habituel local, en nombre d'écarts types robustes."""
    if len(db) == 0:
        return db
    half = max(2, int(round(context_s / hop)))
    base = _rolling(db, half, np.median)
    mad = _rolling(np.abs(db - base), half, np.median)
    spread = np.maximum(1.4826 * mad, 2.0)
    return np.clip((db - base) / spread, 0.0, 8.0)


def find_windows(db: np.ndarray, hop: float, n: int, pre: float, post: float,
                 min_len: float, max_len: float) -> list[Window]:
    """Les `n` fenêtres les plus intenses, sans chevauchement, triées par score."""
    total = len(db) * hop
    if total <= 0 or n <= 0:
        return []
    if total <= max_len:
        return [Window(0.0, total, total / 2, 0.0)]
    x = excitement(db, hop)
    k = max(1, int(round(6.0 / hop)))
    smooth = np.convolve(x, np.ones(k) / k, mode="same")
    if float(smooth.max()) < 0.15:
        # Son plat : on répartit les fenêtres régulièrement.
        length = min(max_len, max(min_len, pre + post))
        out = []
        for i in range(n):
            center = total * (i + 0.5) / n
            s = float(np.clip(center - length / 2, 0, max(0.0, total - length)))
            out.append(Window(s, s + length, center, 0.0))
        return out
    gap = max(1, int(round(min(max_len, pre + post) / hop)))
    work = smooth.copy()
    out: list[Window] = []
    for _ in range(n):
        i = int(np.argmax(work))
        if work[i] <= 0.15:
            break
        lo, hi = max(0, i - k), min(len(x), i + k + 1)
        peak_t = (lo + int(np.argmax(x[lo:hi]))) * hop
        s, e = max(0.0, peak_t - pre), min(total, peak_t + post)
        if e - s < min_len:
            e = min(total, s + min_len)
            s = max(0.0, e - min_len)
        if e - s > max_len:
            e = s + max_len
        out.append(Window(float(s), float(e), float(peak_t), float(smooth[i])))
        work[max(0, i - gap): i + gap + 1] = -1.0
    return out


def find_peaks(db: np.ndarray, hop: float, max_n: int = 3, min_gap: float = 4.0,
               thresh: float = 2.5) -> list[float]:
    """Instants d'impact (début de la montée), du plus fort au plus faible."""
    if len(db) < 4 or max_n <= 0:
        return []
    x = excitement(db, hop, context_s=12.0)
    work = x.copy()
    gap = max(1, int(round(min_gap / hop)))
    back = max(1, int(round(0.6 / hop)))
    peaks: list[float] = []
    for _ in range(max_n):
        i = int(np.argmax(work))
        if work[i] < thresh:
            break
        j = i
        while j > 0 and i - j < back and x[j - 1] > 0.5 * x[i]:
            j -= 1
        peaks.append(j * hop)
        work[max(0, i - gap): i + gap + 1] = -1.0
    return peaks


def motion_profile(path: str | Path, ss: float, dur: float, fps: float = 10.0,
                   w: int = 64, h: int = 36) -> np.ndarray:
    """Mouvement à l'image : écart moyen (0 à 255) entre deux images successives, `fps` fois par seconde.

    Calculé sur une image réduite en gris : quelques secondes pour un clip entier.
    """
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
           "-an", "-vf", f"fps={fps:g},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError):
        return np.zeros(0, dtype=np.float32)
    raw = np.frombuffer(p.stdout or b"", dtype=np.uint8)
    n = len(raw) // (w * h)
    if n < 2:
        return np.zeros(0, dtype=np.float32)
    frames = raw[: n * w * h].reshape(n, h, w).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return np.concatenate([diff[:1], diff]).astype(np.float32)


def black_lead(path: str | Path, ss: float, dur: float = 4.0) -> float:
    """Secondes d'écran noir au tout début de [ss, ss + dur] (chargement, fondu) ; 0 sinon."""
    cmd = ["ffmpeg", "-v", "info", "-nostdin", "-hide_banner", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}",
           "-i", str(path), "-an", "-vf", "blackdetect=d=0.1:pix_th=0.08:pic_th=0.97", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=120, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    for line in (p.stderr or "").splitlines():
        if "black_start:" in line:
            try:
                start = float(line.split("black_start:")[1].split()[0])
                end = float(line.split("black_end:")[1].split()[0])
            except (IndexError, ValueError):
                continue
            return end if start < 0.05 else 0.0
    return 0.0



def _gray_region(path: str | Path, t: float, region: dict, width: int = 640) -> np.ndarray | None:
    """Image en gris de la zone `region` (fractions x, y, w, h) à l'instant t, large de `width` px."""
    x, y, w, h = (max(0.0, float(region[k])) for k in ("x", "y", "w", "h"))
    height = max(32, int(round(width * h / max(w, 1e-3) * 9 / 16 / 2)) * 2)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-frames:v", "1", "-an",
           "-vf", f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},scale={width}:{height},format=gray",
           "-f", "rawvideo", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if len(p.stdout or b"") < width * height:
        return None
    return np.frombuffer(p.stdout[: width * height], dtype=np.uint8).reshape(height, width)


def _face_finders():
    """Détecteurs de visages d'OpenCV 4 (de face, de face bis, de profil) ; [] s'ils manquent."""
    try:
        import cv2
    except ImportError:
        return []
    root = Path(getattr(getattr(cv2, "data", None), "haarcascades", "") or "")
    out = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml",
                 "haarcascade_profileface.xml"):
        if (root / name).exists():
            out.append((name, cv2.CascadeClassifier(str(root / name))))
    return out


def fit_webcam(path: str | Path, start: float, end: float, box: dict, n: int = 8):
    """Vérifie le cadre de webcam donné par l'IA et le recentre sur le visage du streamer.

    L'IA se trompe parfois de cadre (le chat, un mur) ou coupe le visage au bord. On cherche un
    visage dans son cadre élargi de moitié de chaque côté, sur `n` images prises après l'accroche.
    Renvoie :
      - le cadre recentré sur le visage (tête et épaules, proportions du bandeau du haut) ;
      - None si un visage n'apparaît pas sur au moins la moitié des images (cadre douteux) ;
      - le cadre de l'IA tel quel si les détecteurs ne sont pas installés.
    """
    finders = _face_finders()
    if not finders:
        return box
    import cv2

    bx, by, bw, bh = (float(box[k]) for k in ("x", "y", "w", "h"))
    rx, ry = max(0.0, bx - bw / 2), max(0.0, by - bh / 2)
    rw, rh = min(1.0, bx + 1.5 * bw) - rx, min(1.0, by + 1.5 * bh) - ry
    region = {"x": rx, "y": ry, "w": rw, "h": rh}
    first = start + min(4.0, max(0.0, (end - start) / 3))     # l'accroche couvre le bas de la webcam au début
    frames, dets = 0, []
    for i in range(n):
        img = _gray_region(path, first + (end - 0.3 - first) * i / max(1, n - 1), region)
        if img is None:
            continue
        frames += 1
        img = cv2.equalizeHist(img)
        hh, ww = img.shape
        small = max(24, int(min(hh, ww) * 0.08))
        faces = []
        for name, finder in finders:
            for flip in ((False, True) if "profile" in name else (False,)):
                pic = cv2.flip(img, 1) if flip else img
                for (x, y, w, h) in finder.detectMultiScale(pic, 1.08, 5, minSize=(small, small)):
                    x = ww - x - w if flip else x
                    faces.append((w * h, x + w / 2, y + h / 2, h))
        if faces:
            _, cx, cy, fh = max(faces)                         # le plus grand visage de l'image
            dets.append((rx + rw * cx / ww, ry + rh * cy / hh, rh * fh / hh))
    if frames < 4 or len(dets) < max(3, (frames + 1) // 2):
        return None
    cx = float(np.median([d[0] for d in dets]))
    cy = float(np.median([d[1] for d in dets]))
    fh = float(np.median([d[2] for d in dets]))
    if bx + 0.2 * bw <= cx <= bx + 0.8 * bw and by + 0.12 * bh <= cy <= by + 0.75 * bh:
        return box                                             # visage bien placé : le cadre de l'IA est bon
    # Visage au bord ou hors du cadre : même taille que celui de l'IA (plus grand si le visage
    # déborde), recentré sur le visage, sans sortir de l'image.
    h = min(1.0, max(bh, 3.0 * fh))
    w = min(1.0, max(bw, (1080 / 700) * h * 9 / 16))
    x = min(max(0.0, cx - w / 2), 1.0 - w)
    y = min(max(0.0, cy - 0.42 * h), 1.0 - h)
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
