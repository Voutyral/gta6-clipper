"""Montage vertical 1080x1920 avec ffmpeg.

Tout ce qui est ajouté sert le spectateur : accroche, sous-titres, aperçu du moment
fort en ouverture, zoom et secousse brefs sur l'impact, bruitages, musique sous la voix.
Rien ici ne modifie l'image ou le son d'origine pour tromper une détection.
"""
from __future__ import annotations

import hashlib
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import captions, cuts, sfx
from .config import ROOT
from .util import CmdError, log, probe_media, run

W, H = 1080, 1920
MUSIC_EXT = (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus")


@dataclass
class Plan:
    """Un clip à monter. Tous les temps sont ceux du fichier `src`."""
    src: Path
    start: float
    end: float
    words: list = field(default_factory=list)
    peaks: list = field(default_factory=list)   # du plus fort au plus faible
    hook: str = ""
    context: str = ""
    credit: str = ""
    title: str = ""
    description: str = ""
    tags: list = field(default_factory=list)
    layout: str = "auto"
    preset: dict = field(default_factory=dict)
    flags: set = field(default_factory=set)
    score: float = 0.0
    what: str = ""
    risks: list = field(default_factory=list)
    source_url: str = ""
    source_time: float = 0.0   # position du début du fichier dans la vidéo d'origine
    lang: str = ""
    ai_score: float | None = None
    cuts: list = field(default_factory=list)   # plages [début, fin] à retirer (temps source)


def _facecam(preset: dict | None):
    fc = (preset or {}).get("facecam")
    try:
        x, y, w, h = (float(fc[k]) for k in ("x", "y", "w", "h"))
    except Exception:
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1.001 or y + h > 1.001:
        return None
    return x, y, w, h


def pick_layout(plan: Plan, info: dict, ed: dict) -> str:
    want = (plan.layout or "auto").lower()
    if want == "auto":
        want = str((plan.preset or {}).get("layout") or ed.get("layout", "auto")).lower()
    if info["width"] / max(1, info["height"]) <= 0.8:
        return "fill"   # source déjà verticale
    has_cam = _facecam(plan.preset) is not None
    if want == "auto":
        want = "split" if has_cam else "blur"
    if want == "split" and not has_cam:
        want = "blur"
    return want if want in ("blur", "crop", "split", "fill") else "blur"


def game_x(cam_x: float, cam_w: float) -> str:
    """Abscisse du recadrage du jeu : centré, mais décalé pour ne pas montrer la webcam en double."""
    centered = f"(iw-{W})/2"
    if cam_x + cam_w / 2 >= 0.5:          # webcam à droite : le bord droit s'arrête avant elle
        want = f"min({centered},{cam_x:.4f}*iw-{W})"
    else:                                  # webcam à gauche : le bord gauche commence après elle
        want = f"max({centered},{cam_x + cam_w:.4f}*iw)"
    return f"max(0,min(iw-{W},{want}))"


def layout_chain(src: str, out: str, layout: str, ed: dict, preset: dict, fps_s: str, tag: str) -> str:
    """Chaîne ffmpeg qui transforme [src] en image verticale [out]."""
    tail = f"fps={fps_s},setsar=1,format=yuv420p[{out}]"
    if layout == "fill":
        return (f"[{src}]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},{tail}")
    if layout == "crop":
        return f"[{src}]scale=-2:{H}:flags=lanczos,crop={W}:{H},{tail}"
    if layout == "split":
        x, y, w, h = _facecam(preset)
        cam_h = 700
        game_h = H - cam_h
        return (f"[{src}]split=2[c{tag}][g{tag}];"
                f"[c{tag}]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
                f"scale={W}:{cam_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{cam_h}[cc{tag}];"
                f"[g{tag}]scale=-2:{game_h}:flags=lanczos,crop={W}:{game_h}:'{game_x(x, w)}':0[gg{tag}];"
                f"[cc{tag}][gg{tag}]vstack=inputs=2,{tail}")
    zoom = max(1.0, float(ed.get("zoom", 1.25)))
    fg_w = int(round(W * zoom / 2) * 2)
    return (f"[{src}]split=2[b{tag}][f{tag}];"
            f"[b{tag}]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            f"gblur=sigma=9,scale={W}:{H}:flags=bilinear,eq=brightness=-0.12[bb{tag}];"
            f"[f{tag}]scale={fg_w}:-2:flags=lanczos,crop='min(iw,{W})':ih[ff{tag}];"
            f"[bb{tag}][ff{tag}]overlay=(W-w)/2:(H-h)/2-30,{tail}")


def punch_filter(punches: list[float], ed: dict, fps_s: str) -> str:
    """Zoom rapide puis secousse brève à chaque impact, et rien entre deux."""
    amount = max(0.0, float(ed.get("punch_zoom", 1.12)) - 1.0)
    shake = float(ed.get("shake_px", 12))
    z = "1" + "".join(
        f"+{amount:.3f}*(clip((it-{p:.3f})/0.10,0,1)-clip((it-{p + 0.55:.3f})/0.30,0,1))"
        for p in punches)
    gate = "+".join(f"between(it,{p:.3f},{p + 0.38:.3f})" for p in punches)
    x = f"iw/2-(iw/zoom/2)+{shake:.1f}*sin(2*PI*23*it)*({gate})"
    y = f"ih/2-(ih/zoom/2)+{shake:.1f}*cos(2*PI*29*it)*({gate})"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps_s}"


def _pick_music(seed: str):
    folder = ROOT / "assets" / "music"
    tracks = sorted(p for p in folder.glob("*") if p.suffix.lower() in MUSIC_EXT) if folder.exists() else []
    if not tracks:
        return None
    return random.Random(int(hashlib.sha1(seed.encode()).hexdigest(), 16)).choice(tracks)


def render(plan: Plan, out: str | Path, cfg: dict, workdir: str | Path) -> dict:
    """Rend le Short et retourne ce qui a été appliqué. Repli en version simple si besoin."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _render(plan, out, cfg, workdir, set(plan.flags))
    except CmdError as exc:
        if "brut" in plan.flags:
            raise
        log(f"Montage complet refusé par ffmpeg, second essai en version simple.\n{exc}")
        facts = _render(plan, out, cfg, workdir, set(plan.flags) | {"brut"})
        facts["fallback"] = True
        return facts


def _render(plan: Plan, out: Path, cfg: dict, workdir: Path, flags: set) -> dict:
    ed = cfg["edit"]
    src = Path(plan.src).resolve()
    info = probe_media(src)
    if not info["has_video"]:
        raise RuntimeError("la source n'a pas de piste vidéo")
    start = max(0.0, plan.start)
    end = min(plan.end, info["duration"]) if info["duration"] else plan.end
    dur = end - start
    if dur < 2:
        raise RuntimeError("extrait trop court")
    raw = "brut" in flags
    fps = min(60.0, max(24.0, info["fps"]))
    fps_s = f"{fps:.3f}".rstrip("0").rstrip(".")
    layout = pick_layout(plan, info, ed)

    # Coupes dynamiques : la partie principale devient une suite de morceaux gardés.
    segs = [(start, end)]
    if plan.cuts and not raw and "nocut" not in flags:
        segs = cuts.keep_segments(start, end, plan.cuts)
    main = sum(e - s for s, e in segs)

    inside = [p for p in plan.peaks if start + 1.0 <= p <= end - 1.0 and cuts.remap(p, segs) is not None]
    punch_src: list[float] = []   # temps source des impacts
    if ed.get("punch", True) and not raw and "nopunch" not in flags:
        punch_src = sorted(inside[: int(ed.get("max_punches", 3))])
    punches = [cuts.remap(p, segs) for p in punch_src]   # temps dans la partie principale

    mode = str(ed.get("teaser", "auto")).lower()
    if raw or "noteaser" in flags:
        mode = "off"
    elif "teaser" in flags:
        mode = "on"
    td = float(ed.get("teaser_s", 1.8))
    teaser_at = None
    if mode in ("on", "auto", "true") and inside and main >= 16:
        strongest = cuts.remap(inside[0], segs)
        if (mode != "auto" or strongest >= 8.0) and strongest - 0.35 + td <= main:
            teaser_at = max(start, inside[0] - 0.35)
    offset = td if teaser_at is not None else 0.0
    total = main + offset

    words = []
    if cfg["captions"].get("enabled", True) and "nosub" not in flags:
        last, before = len(segs) - 1, 0.0
        for k, (s0, e0) in enumerate(segs):
            lo = s0 - (0.05 if k == 0 else 0.0)
            hi = e0 + (0.3 if k == last else 0.0)
            for w in plan.words:
                mid = (w["s"] + w["e"]) / 2
                if (w["s"] >= lo and w["e"] <= hi) or (len(segs) > 1 and s0 <= mid < e0):
                    ws = before + max(w["s"], s0) - s0 + offset
                    we = before + min(w["e"], e0 if k < last else w["e"]) - s0 + offset
                    words.append(dict(w, s=ws, e=min(total, max(we, ws + 0.04))))
            before += e0 - s0
        words.sort(key=lambda w: w["s"])
    prefix = (plan.preset or {}).get("credit_prefix", ed.get("credit_prefix", "Clip :"))
    credit = f"{prefix or ''} {plan.credit}".strip() if plan.credit else ""
    ass = captions.build_ass(
        words, hook=plan.hook, context=plan.context, credit=credit, total=total, cfg=cfg,
        hook_until=offset + float(ed.get("hook_seconds", 3.5)),
        hook_pos=(540, 700) if layout == "split" else None)
    (workdir / "subs.ass").write_text(ass, encoding="utf-8")
    fonts = workdir / "fonts"
    if not fonts.exists():
        shutil.copytree(ROOT / "assets" / "fonts", fonts)

    events: list[tuple[Path, float, float]] = []
    if ed.get("sfx", True) and not raw and "nosfx" not in flags:
        files = sfx.ensure(workdir)
        gain = float(ed.get("sfx_gain_db", -9))
        if teaser_at is not None:
            events.append((files["whoosh"], max(0.0, td - 0.30), gain))
        events.extend((files["impact"], max(0.0, p + offset - 0.02), gain) for p in punches)

    track = None
    if str(ed.get("music", "auto")).lower() not in ("off", "false", "none") and not raw \
            and "nomusic" not in flags:
        track = _pick_music(f"{src.name}:{start:.1f}")

    cmd: list[str] = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
    fc: list[str] = []
    n_in = 0
    v_segs: list[str] = []
    a_segs: list[str] = []

    def add_segment(ss: float, d: float, tag: str, punch_list: list[float], flash: bool,
                    zoom: float = 1.0, fade_out: float = 0.12) -> None:
        nonlocal n_in
        cmd.extend(["-ss", f"{ss:.3f}", "-t", f"{d:.3f}", "-i", str(src)])
        i, n_in = n_in, n_in + 1
        last = f"v{tag}0"
        fc.append(layout_chain(f"{i}:v", last, layout, ed, plan.preset, fps_s, tag))
        if zoom > 1.0:   # coupe franche : léger changement de cadre, comme un « jump cut »
            zw, zh = int(W * zoom / 2) * 2, int(H * zoom / 2) * 2
            fc.append(f"[{last}]scale={zw}:{zh},crop={W}:{H},setsar=1[v{tag}z]")
            last = f"v{tag}z"
        if punch_list:
            fc.append(f"[{last}]{punch_filter(punch_list, ed, fps_s)},format=yuv420p[v{tag}1]")
            last = f"v{tag}1"
        if flash:
            fc.append(f"[{last}]fade=t=in:st=0:d=0.12:color=white[v{tag}2]")
            last = f"v{tag}2"
        v_segs.append(last)
        if info["has_audio"]:
            fc.append(f"[{i}:a]aresample=48000:async=1:first_pts=0,"
                      f"aformat=sample_fmts=fltp:channel_layouts=stereo,afade=t=in:d=0.03,"
                      f"afade=t=out:st={max(0.0, d - fade_out):.3f}:d={fade_out:.2f}[a{tag}]")
        else:
            fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f}[a{tag}]")
        a_segs.append(f"a{tag}")

    if teaser_at is not None:
        add_segment(teaser_at, td, "t", [], False)
    jump = float(ed.get("jump_zoom", 1.07))
    for k, (s0, e0) in enumerate(segs):
        local = [p - s0 for p in punch_src if s0 <= p <= e0]
        add_segment(s0, e0 - s0, f"m{k}", local, k == 0 and teaser_at is not None,
                    zoom=jump if k % 2 == 1 else 1.0, fade_out=0.12 if k == len(segs) - 1 else 0.04)

    n = len(v_segs)
    if n > 1:
        fc.append("".join(f"[{v}]" for v in v_segs) + f"concat=n={n}:v=1:a=0[vc]")
        fc.append("".join(f"[{a}]" for a in a_segs) + f"concat=n={n}:v=0:a=1[ac]")
    else:
        fc.append(f"[{v_segs[0]}]null[vc]")
        fc.append(f"[{a_segs[0]}]anull[ac]")
    fc.append("[vc]ass=filename=subs.ass:fontsdir=fonts,format=yuv420p[v]")

    mix = ["ac"]
    if track is not None:
        cmd.extend(["-stream_loop", "-1", "-i", str(track)])
        mi, n_in = n_in, n_in + 1
        fc.append("[ac]asplit=2[am][akey]")
        fc.append(f"[{mi}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  f"atrim=0:{total:.3f},afade=t=in:d=0.6,"
                  f"afade=t=out:st={max(0.0, total - 1.0):.3f}:d=1.0,"
                  f"volume={float(ed.get('music_gain_db', -21)):.1f}dB[mus0]")
        fc.append("[mus0][akey]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[mus]")
        mix = ["am", "mus"]
    for k, (path, at, gain) in enumerate(events):
        cmd.extend(["-i", str(path)])
        si, n_in = n_in, n_in + 1
        fc.append(f"[{si}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                  f"adelay={int(at * 1000)}:all=1,volume={gain:.1f}dB[s{k}]")
        mix.append(f"s{k}")
    last_a = mix[0]
    if len(mix) > 1:
        fc.append("".join(f"[{x}]" for x in mix)
                  + f"amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0[mix]")
        last_a = "mix"
    fc.append(f"[{last_a}]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")

    # Débit plafonné : le fichier reste sous 50 Mo, léger à télécharger au téléphone.
    kbps = max(2500, min(9000, int(46 * 8192 / max(total, 1.0)) - 180))
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", str(ed.get("x264_preset", "veryfast")), "-crf", "20",
            "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-g", str(int(round(fps * 2))),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    run(cmd, cwd=workdir, timeout=2400)
    return {"duration": total, "layout": layout, "teaser": teaser_at is not None,
            "punches": len(punches), "sfx": len(events), "music": track.name if track else "",
            "captions": len(words), "fallback": False, "cuts": len(segs) - 1,
            "cut_s": round(dur - main, 1)}


def contact_sheet(src: str | Path, ss: float, dur: float, out_jpg: str | Path,
                  cols: int = 3, rows: int = 2) -> Path | None:
    """Planche d'images de l'extrait, pour qu'une IA « voie » ce qui se passe."""
    n = cols * rows
    try:
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}",
             "-i", str(src), "-vf", f"fps={n}/{max(dur, 1.0):.3f},scale=400:-2,tile={cols}x{rows}:padding=4",
             "-frames:v", "1", "-q:v", "4", str(out_jpg)], quiet=True)
        return Path(out_jpg)
    except CmdError:
        return None


def make_proxy(src: str | Path, ss: float, dur: float, out_mp4: str | Path) -> Path | None:
    """Copie légère (360p) de l'extrait, pour l'envoyer à une IA qui lit la vidéo."""
    try:
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}",
             "-i", str(src), "-vf", "scale=-2:360,fps=15", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart",
             str(out_mp4)], quiet=True)
        return Path(out_mp4)
    except CmdError:
        return None
