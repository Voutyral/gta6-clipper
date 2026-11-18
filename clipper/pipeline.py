"""Enchaînement complet : demande → source → moments forts → montage → contrôle → livraison."""
from __future__ import annotations

import json
import os
import re
import shutil
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import ai, captions, deliver, detect, fetch, transcribe, vocab, wordfix, youtube
from . import verify as qc
from .config import match_streamer
from .render import Plan, contact_sheet, make_proxy, render
from .request import ClipRequest, parse_requests
from .util import fmt_ts, log, probe_media, run, slugify

EXAMPLE = "https://www.twitch.tv/videos/123456 1:02:30-1:03:10 @streamer"


@dataclass
class Candidate:
    src: Path
    start: float
    end: float
    peak: float | None
    score: float
    origin: float            # position du début du fichier dans la vidéo d'origine
    locked: bool = False     # plage choisie par l'utilisateur : on ne la retaille pas
    words: list = field(default_factory=list)
    lang: str = ""


@dataclass
class Result:
    path: Path
    title: str
    description: str
    tags: list
    hook: str
    go: bool
    problems: list
    notes: list
    duration: float
    size_mb: float
    credit: str
    source_url: str
    facts: dict
    provider: str = ""
    paid: bool = False
    lang: str = "fr"
    link: str = ""
    yt: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)   # infos pour la galerie


# ---------------------------------------------------------------- 1. Source et candidats

def acquire(req: ClipRequest, info: dict, cfg: dict, work: Path) -> list[Candidate]:
    """Télécharge le strict nécessaire et retourne les passages candidats."""
    det, fe = cfg["detection"], cfg["fetch"]
    n = max(1, req.count or int(det["clips_per_video"]))
    # Des passages en plus ne servent que si une IA peut les départager.
    use_ai = bool(ai.providers(cfg)) and "noai" not in req.flags
    want = n + (int(det.get("extra_candidates", 2)) if use_ai else 0)
    pre, post = float(det["pre_peak_s"]), float(det["post_peak_s"])
    cmin, cmax = float(det["clip_min_s"]), float(det["clip_max_s"])
    local = fetch.is_local(req.url)
    duration = float(info["duration"]) if info.get("duration") else None
    max_h = int(fe.get("max_height", 1080))

    def windows(src: Path, lo: float, hi: float, origin: float) -> list[Candidate]:
        db = detect.energy_profile(src, hop=0.5, ss=lo, dur=hi - lo)
        found = detect.find_windows(db, 0.5, want, pre, post, cmin, cmax)
        return [Candidate(src, w.start + lo, w.end + lo, w.peak + lo, w.score, origin) for w in found]

    span = None
    if req.start is not None and req.end is not None:
        span = (req.start, req.end)
    elif req.moment is not None:
        span = (max(0.0, req.moment - pre), req.moment + post)

    if span:
        s, e = span
        if duration:
            e = min(e, duration)
        if e - s < 3:
            raise fetch.FetchError("La plage demandée est vide ou dépasse la fin de la vidéo.")
        if local:
            src, origin = Path(req.url), 0.0
        else:
            origin = max(0.0, s - 2.0)
            src = fetch.download_section(req.url, origin, e + 2.0, work / "src", "section", max_h)
        real = probe_media(src)["duration"]
        ls, le = s - origin, min(e - origin, real or e - origin)
        if req.start is None:   # un seul instant donné : fenêtre autour, l'IA peut resserrer
            return [Candidate(src, ls, le, req.moment - origin, 1.0, origin)]
        if le - ls <= cmax + 1.0:
            return [Candidate(src, ls, le, None, 1.0, origin, locked=True)]
        return windows(src, ls, le, origin)

    if local or duration is None or duration <= float(fe["full_download_max_s"]):
        src = Path(req.url) if local else fetch.download_full(req.url, work / "src", "full", max_h)
        real = probe_media(src)["duration"]
        if real <= float(det["keep_whole_if_under_s"]):
            return [Candidate(src, 0.0, real, None, 1.0, 0.0)]
        return windows(src, 0.0, real, 0.0)

    # Longue vidéo : on écoute la piste son, puis on ne télécharge que les passages retenus.
    log("Longue vidéo : repérage des moments forts sur la piste son.")
    audio = fetch.download_audio(req.url, work / "src", "scan", float(fe["max_scan_s"]))
    db = detect.energy_profile(audio, hop=0.5)
    out: list[Candidate] = []
    for i, w in enumerate(detect.find_windows(db, 0.5, want, pre, post, cmin, cmax)):
        a = max(0.0, w.start - 2.0)
        try:
            src = fetch.download_section(req.url, a, w.end + 2.0, work / "src", f"cand{i}", max_h)
        except fetch.FetchError as exc:
            log(f"Passage à {fmt_ts(w.start)} ignoré : {exc}")
            continue
        out.append(Candidate(src, w.start - a, w.end - a, w.peak - a, w.score, a))
    if not out:
        raise fetch.FetchError("Aucun passage n'a pu être téléchargé.")
    return out


# ---------------------------------------------------------------- 2. Choix et habillage

def snap_to_words(start: float, end: float, words: list[dict], lo: float, hi: float) -> tuple[float, float]:
    """Évite de couper au milieu d'un mot."""
    for w in words:
        if w["s"] < start < w["e"]:
            start = max(lo, w["s"] - 0.08)
            break
    for w in words:
        if w["s"] < end < w["e"] + 0.05:
            end = min(hi + 0.3, w["e"] + 0.2)
            break
    return start, end


def clean_hook(text: str) -> str:
    text = " ".join(captions.strip_emoji(text).replace('"', "").split())
    words = text.split()
    return " ".join(words[:6])[:40].strip()


def hashtags_for(game: str, cfg: dict) -> list[str]:
    meta = cfg["metadata"]
    by_game = meta.get("hashtags_by_game") or {}
    def norm(t) -> str:   # Kick ajoute « (GTA) » au nom du jeu
        return re.sub(r"\s*\(.*\)\s*$", "", str(t or "")).strip().lower()

    for name, tags in by_game.items():
        if game and norm(game) == norm(name):
            return list(tags) + list(meta.get("hashtags_common") or [])
    return list(meta["hashtags"])


def build_description(desc: str, credit: str, url: str, cfg: dict, game: str = "") -> str:
    lines = [desc.strip()] if desc.strip() else []
    if not fetch.is_local(url):
        lines.append(f"Source : {credit} ({url})" if credit else f"Source : {url}")
    elif credit:
        lines.append(f"Source : {credit}")
    lines.append(" ".join(hashtags_for(game, cfg)))
    return "\n\n".join(lines)


def facecam_box(raw) -> dict | None:
    """Webcam repérée par l'IA, au format [ymin, xmin, ymax, xmax] sur 1000. None si douteuse."""
    try:
        y0, x0, y1, x1 = (float(v) for v in raw)
    except Exception:
        return None
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    x, y, w, h = x0 / 1000, y0 / 1000, (x1 - x0) / 1000, (y1 - y0) / 1000
    aspect = (w / h) * (16 / 9)          # proportions réelles sur une image 16:9
    if not (0.012 <= w * h <= 0.30 and 0.7 <= aspect <= 2.6):
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def build_plan(c: Candidate, ev: dict | None, peaks: list[float], req: ClipRequest, info: dict,
               cfg: dict, preset: dict, credit: str, idx: int) -> Plan:
    det, meta = cfg["detection"], cfg["metadata"]
    dur = c.end - c.start
    start, end, score = c.start, c.end, float(c.score)
    ai_score = None
    hook, context, title, desc, what = req.hook or "", "", "", "", ""
    tags: list[str] = []
    risks: list[str] = []
    if ev:
        try:
            ai_score = float(ev.get("score", 0))
            score = ai_score + min(0.9, c.score / 10.0)   # le son départage
        except (TypeError, ValueError):
            pass
        box = facecam_box(ev.get("facecam"))
        if (box and cfg["edit"].get("auto_facecam", True) and not req.layout
                and not preset.get("facecam") and not preset.get("layout")):
            preset = dict(preset, facecam=box, layout="split")
        if not c.locked:
            try:
                s, e = float(ev.get("start", 0)), float(ev.get("end", dur))
                s, e = max(0.0, min(s, dur)), max(0.0, min(e, dur))
                if float(det["clip_min_s"]) <= e - s <= max(float(det["clip_max_s"]), dur) + 0.01:
                    start, end = c.start + s, c.start + e
            except (TypeError, ValueError):
                pass
        hook = hook or str(ev.get("hook") or "")
        context = str(ev.get("context") or "")
        title = str(ev.get("title") or "")
        desc = str(ev.get("description") or "")
        what = str(ev.get("what") or "")
        tags = [str(t).lstrip("#").strip() for t in (ev.get("tags") or []) if str(t).strip()][:12]
        risks = [str(r) for r in (ev.get("risks") or []) if isinstance(r, str)]
    if not c.locked:
        start, end = snap_to_words(start, end, c.words, c.start, c.end)
    if not hook:
        hook = meta["fallback_hooks"][idx % len(meta["fallback_hooks"])]
    if not title:
        base = (info.get("title") or meta["game"]).strip()
        who = credit.lstrip("@")
        title = f"{who} : {base}" if who and who.lower() not in base.lower() else base
    if not tags:
        tags = [meta["game"], "gaming", "clip"] + ([credit.lstrip("@")] if credit else [])
    return Plan(
        src=c.src, start=start, end=end, words=c.words, peaks=peaks, hook=clean_hook(hook),
        context=context.strip()[:60], credit=credit, title=" ".join(title.split())[:95],
        description=build_description(desc, credit, info.get("webpage_url") or req.url, cfg, req.game_hint),
        tags=tags, layout=req.layout or "auto", preset=preset, flags=set(req.flags), score=score,
        what=what, risks=risks, source_url=info.get("webpage_url") or req.url,
        source_time=c.origin + start, lang=c.lang, ai_score=ai_score)


def ai_fix_words(c: Candidate, ev: dict, req: ClipRequest, cap: dict) -> bool:
    """Gemini a écouté le clip : on reprend sa transcription (noms, argot) sur nos temps de mots.

    Renvoie False si le passage doit être écarté (langue parlée hors de celles voulues).
    """
    spoken = str(ev.get("spoken_lang") or "").strip().lower()[:2]
    fixed = str(ev.get("transcript") or "").strip()
    if spoken:
        # Gemini entend le son lui-même : son avis vaut aussi pour les langues que Parakeet
        # ne sait pas écrire (hindi, arabe, turc…). Deux ou trois mots isolés ne comptent pas.
        if req.langs and spoken not in req.langs and len(fixed.split()) >= 4:
            log(f"Passage écarté : l'IA entend du « {spoken} », langues voulues {req.langs}.")
            return False
        c.lang = spoken
    if not (cap.get("ai_fix", True) and fixed and c.words):
        return True
    new, ratio, edits = wordfix.align(c.words, fixed)
    if new is c.words:
        log(f"Correction des sous-titres ignorée : trop loin de ce qui a été entendu ({ratio:.0%}).")
    else:
        c.words = new   # même sans mot changé, la ponctuation de l'IA aide à couper les phrases
        log(f"Sous-titres vérifiés par l'IA (ressemblance {ratio:.0%}) : "
            + (f"{len(edits)} correction(s) : " + wordfix.describe(edits) if edits else "rien à corriger."))
    return True


def plan_candidates(cands: list[Candidate], req: ClipRequest, info: dict, cfg: dict, preset: dict,
                    credit: str, work: Path, n: int) -> tuple[list[Plan], str]:
    """Transcrit, évalue (IA si disponible) et retient les `n` meilleurs passages."""
    lang_out = req.lang or cfg["metadata"]["language"]
    use_ai = bool(ai.providers(cfg)) and "noai" not in req.flags
    cap = cfg["captions"]
    words_vocab = vocab.for_clip(vocab.load_vocab(), credit, req.meta.get("streamer", ""),
                                 info.get("channel", ""))
    if not use_ai:
        cands = sorted(cands, key=lambda c: c.score, reverse=True)[:n]
    plans: list[Plan] = []
    used = ""
    for i, c in enumerate(cands):
        dur = c.end - c.start
        c.words, c.lang = transcribe.transcribe(
            c.src, c.start, dur, model_name=cap["whisper_model"], workdir=work,
            engine=cap.get("engine", "parakeet"), vocab=words_vocab, cfg=cfg)
        if req.langs and c.lang and c.lang not in req.langs and len(c.words) >= 6:
            log(f"Passage écarté : on y parle « {c.lang} », langues voulues {req.langs}.")
            continue
        if req.langs and not c.lang and len(c.words) >= 3 and not use_ai:
            log("Passage écarté : langue impossible à vérifier sans IA.")
            continue
        db = detect.energy_profile(c.src, hop=0.1, ss=c.start, dur=dur)
        peaks = [c.start + p for p in detect.find_peaks(db, 0.1, int(cfg["edit"]["max_punches"]))]
        if c.peak is not None and not peaks:
            peaks = [c.peak]
        ev = None
        if use_ai:
            video = None
            if "gemini" in ai.providers(cfg):
                video = make_proxy(c.src, c.start, dur, work / f"proxy{i}.mp4")
            sh = contact_sheet(c.src, c.start, dur, work / f"sheet{i}.jpg")
            ev, name = ai.evaluate(
                cfg, text=transcribe.words_to_lines(c.words, c.start), dur=dur,
                peak=(c.peak - c.start) if c.peak is not None else None,
                title=info.get("title", ""), credit=credit, lang=lang_out, video=video,
                sheet=sh.read_bytes() if sh else None, game=req.game_hint, vocab=words_vocab)
            used = used or name
            if ev and name == "gemini" and video is not None:
                if not ai_fix_words(c, ev, req, cap):
                    continue
        plans.append(build_plan(c, ev, peaks, req, info, cfg, preset, credit, i))
    plans.sort(key=lambda p: p.score, reverse=True)
    return plans[:n], used


# ---------------------------------------------------------------- 3. Une demande de bout en bout

def process_request(req: ClipRequest, cfg: dict, streamers: list[dict], work: Path, outdir: Path,
                    idx: int) -> list[Result]:
    log(f"\n=== Demande {idx + 1} : {req.url}")
    work.mkdir(parents=True, exist_ok=True)
    info = fetch.probe(req.url)
    # Ce que la source a déjà dit (chasse) prime sur ce que yt-dlp devine.
    info.update({k: req.meta[k] for k in ("title", "channel", "handle", "webpage_url") if req.meta.get(k)})
    if info.get("is_live"):
        raise fetch.FetchError("Le live est encore en cours.",
                               "Envoie le lien de la rediffusion ou d'un clip une fois le passage terminé.")
    preset = match_streamer(streamers, req.credit or "", info.get("channel", ""),
                            info.get("channel_id", ""), info.get("handle", ""))
    credit = req.credit or preset.get("credit") or info.get("handle") or ""
    n = max(1, req.count or int(cfg["detection"]["clips_per_video"]))
    cands = acquire(req, info, cfg, work)
    log(f"{len(cands)} passage(s) candidat(s).")
    plans, provider = plan_candidates(cands, req, info, cfg, preset, credit, work, n)
    if req.min_score is not None:
        low = [p for p in plans if p.ai_score is not None and p.ai_score < req.min_score]
        for p in low:
            log(f"Passage écarté : note IA {p.ai_score:g} sous le seuil {req.min_score:g} ({p.what}).")
        plans = [p for p in plans if p not in low]
    results: list[Result] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for k, plan in enumerate(plans):
        name = f"{stamp}-{slugify(info.get('channel') or info.get('title') or 'clip', 24)}-{idx}{k}.mp4"
        out = outdir / name
        facts = render(plan, out, cfg, work / f"render{k}")
        check = qc.verify(out, facts=facts, risks=plan.risks, cfg=cfg, rights_ok=bool(preset))
        log(f"{'GO' if check['go'] else 'NO-GO'} : {name} ({check['duration']:.1f} s, "
            f"{check['size_mb']:.1f} Mo) {check['problems'] or ''}")
        results.append(Result(
            path=out, title=plan.title, description=plan.description, tags=plan.tags,
            hook=plan.hook, go=check["go"], problems=check["problems"], notes=check["notes"],
            duration=check["duration"], size_mb=check["size_mb"], credit=credit,
            source_url=plan.source_url, facts=facts, provider=provider,
            paid=bool(preset.get("paid")), lang=req.lang or cfg["metadata"]["language"],
            meta={"ai_score": plan.ai_score, "what": plan.what, "streamer": req.meta.get("streamer") or credit.lstrip("@"),
                  "platform": req.meta.get("platform", ""), "views": req.meta.get("views"),
                  "source_title": info.get("title", ""), "found_why": req.meta.get("found_why", ""),
                  "layout": facts.get("layout")}))
    return results


def write_sidecars(res: Result) -> tuple[Path, Path | None]:
    """Fiche JSON et vignette à côté du clip : la galerie se construit à partir d'elles."""
    thumb = res.path.with_suffix(".jpg")
    try:
        run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", "1.2", "-i", str(res.path), "-frames:v", "1",
             "-vf", "scale=540:-2", "-q:v", "4", str(thumb)], quiet=True)
    except Exception:
        thumb = None
    card = {
        "file": res.path.name, "thumb": thumb.name if thumb else "", "title": res.title,
        "description": res.description, "tags": res.tags, "hook": res.hook, "go": res.go,
        "problems": res.problems, "notes": res.notes, "credit": res.credit,
        "source_url": res.source_url, "duration": round(res.duration, 1), "size_mb": round(res.size_mb, 1),
        "provider": res.provider, "paid": res.paid,
        "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **res.meta,
    }
    side = res.path.with_suffix(".json")
    side.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    return side, thumb


def deliver_result(res: Result, cfg: dict) -> None:
    d = cfg["delivery"]
    side, thumb = write_sidecars(res)
    if d.get("release", True):
        res.link = deliver.release_upload(res.path)
        for extra in (thumb, side):
            if extra and extra.exists():
                deliver.release_upload(extra)
    if youtube.enabled(cfg):
        if res.go:
            res.yt = youtube.upload(res.path, title=res.title, description=res.description,
                                    tags=res.tags, cfg=cfg, lang=res.lang, paid=res.paid)
        else:
            res.yt = {"error": "non envoyé, le contrôle a rendu NO-GO"}


def run_requests(reqs: list[ClipRequest], cfg: dict, streamers: list[dict], base: Path,
                 outdir: Path) -> tuple[list[Result], list[tuple[str, str, str]]]:
    results: list[Result] = []
    errors: list[tuple[str, str, str]] = []
    for i, req in enumerate(reqs[:8]):
        work = base / f"work{i}"
        try:
            found = process_request(req, cfg, streamers, work, outdir, i)
            for res in found:
                deliver_result(res, cfg)
            results.extend(found)
        except fetch.FetchError as exc:
            errors.append((req.url, str(exc), exc.hint))
            log(f"Échec : {exc} {exc.hint}")
        except Exception as exc:   # une demande ratée ne doit pas bloquer les suivantes
            errors.append((req.url, f"{type(exc).__name__} : {exc}", ""))
            log(traceback.format_exc())
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return results, errors


def run_text(text: str, cfg: dict, streamers: list[dict], base: Path, outdir: Path) -> int:
    """Point d'entrée du workflow : texte libre d'une issue ou d'un lancement manuel."""
    reqs = parse_requests(text)
    if not reqs and os.environ.get("GITHUB_EVENT_NAME") == "issue_comment":
        log("Commentaire sans lien vidéo : rien à faire.")   # simple discussion dans une issue
        return 0
    if not reqs:
        msg = f"Aucun lien trouvé dans la demande. Exemple : `{EXAMPLE}`"
        deliver.issue_comment(msg)
        deliver.step_summary(msg)
        return 1
    results, errors = run_requests(reqs, cfg, streamers, base, outdir)
    report = deliver.markdown_report(results, errors, gallery=True)
    deliver.issue_comment(report)
    deliver.step_summary(report)
    deliver.cleanup_releases(int(cfg["delivery"].get("keep_days", 7)))
    return 0 if results else 1
