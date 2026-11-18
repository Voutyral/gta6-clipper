"""Le chasseur : trouve les clips du moment, garde les meilleurs, les monte et les range.

À chaque passage :
1. lecture des clips les plus regardés des dernières 24 h (Twitch, Kick, streamers suivis) ;
2. tri par vitesse de vues (un clip récent qui décolle passe devant un vieux clip à succès) ;
3. montage des meilleurs, avec une note de l'IA qui écarte les moments faibles ;
4. galerie mise à jour et issue ouverte : le téléphone reçoit une notification.
"""
from __future__ import annotations

import json
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import deliver, fetch
from .config import ROOT, match_streamer
from .pipeline import Result, deliver_result, process_request
from .request import ClipRequest
from .sources import Found, discover
from .util import log

SEEN = ROOT / "state" / "seen.json"


# ---------------------------------------------------------------- Mémoire des clips déjà vus

def load_seen() -> dict:
    try:
        data = json.loads(SEEN.read_text())
    except Exception:
        return {}
    out = {}
    for k, v in data.items():   # ancien format : {id: "AAAA-MM-JJ"}
        out[k] = v if isinstance(v, dict) else {"d": str(v), "s": "", "ok": False}
    return out


def save_seen(seen: dict, keep_days: int = 30) -> None:
    today = datetime.now(timezone.utc).date()
    keep = {}
    for k, v in seen.items():
        try:
            if (today - datetime.fromisoformat(v["d"]).date()).days <= keep_days:
                keep[k] = v
        except Exception:
            continue
    SEEN.parent.mkdir(exist_ok=True)
    SEEN.write_text(json.dumps(keep, indent=0, ensure_ascii=False))


def made_today(seen: dict, streamer: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = streamer.lower()
    return sum(1 for v in seen.values() if v.get("ok") and v.get("d") == today and v.get("s", "").lower() == s)


# ---------------------------------------------------------------- Tri

def rank(found: list[Found], cfg: dict, streamers: list[dict], seen: dict,
         now: datetime | None = None, stats: dict | None = None) -> list[tuple[float, Found, dict, str]]:
    """Clips retenus, du plus prometteur au moins prometteur : (score, clip, fiche streamer, motif).

    `stats`, si fourni, compte les clips écartés par motif (affiché dans le journal de la chasse).
    """
    stats = {} if stats is None else stats

    def drop(reason: str) -> None:
        stats[reason] = stats.get(reason, 0) + 1

    hc = cfg["hunt"]
    now = now or datetime.now(timezone.utc)
    blocked = {str(b).lower().lstrip("@") for b in hc.get("blocked_streamers") or []}
    excluded = [str(w).lower() for w in hc.get("exclude_words") or [] if str(w).strip()]
    wanted_langs = [str(x).lower() for x in hc.get("require_languages") or [] if str(x).strip()]
    out = []
    for f in found:
        preset = match_streamer(streamers, f.streamer, f.streamer_name)
        if f.key in seen:
            drop("déjà vus")
            continue
        if f.streamer.lower() in blocked:
            drop("streamer bloqué")
            continue
        if not float(hc.get("min_duration_s", 8)) <= f.duration <= float(hc.get("max_duration_s", 120)):
            drop("durée hors limites")
            continue
        if hc.get("allowlist_only") and not preset:
            drop("hors streamers.yaml")
            continue
        if f.views < int(preset.get("min_views", hc.get("min_views", 150))):
            drop("pas assez de vues")
            continue
        if f.age_hours(now) > float(hc.get("max_age_hours", 30)):
            drop("trop anciens")
            continue
        title = f.title.lower()
        if any(w in title for w in excluded):
            drop("mot exclu dans le titre")
            continue
        # Vues par heure, avec un amortissement pour ne pas surestimer les clips de dix minutes.
        score = f.views / (f.age_hours(now) + 2.0) ** 0.8
        why = f"{f.views} vues en {f.age_hours(now):.0f} h"
        if preset:
            score *= float(hc.get("allowlist_boost", 2.0))
            why += ", streamer suivi"
        elif wanted_langs and not f.language:
            why += ", langue à vérifier"   # Kick : vérifiée seulement après téléchargement
        elif wanted_langs and f.language not in wanted_langs:
            drop("autre langue")
            continue
        out.append((score, f, preset, why))
    # Langue connue d'abord : un clip dont la langue reste à vérifier ne passe qu'en dernier.
    out.sort(key=lambda t: ("langue à vérifier" in t[3], -t[0]))
    cap = int(hc.get("max_per_streamer_day", 2))
    unknown_cap = int(hc.get("max_unknown_language", 1))
    picked, count, unknown = [], {}, 0
    for item in out:
        s = item[1].streamer.lower()
        if count.get(s, 0) + made_today(seen, s) >= cap:
            drop("quota du streamer atteint")
            continue
        if "langue à vérifier" in item[3]:
            if unknown >= unknown_cap:
                drop("langue inconnue (Kick), un seul essai par chasse")
                continue
            unknown += 1
        count[s] = count.get(s, 0) + 1
        picked.append(item)
    return picked


def to_request(f: Found, preset: dict, cfg: dict, url: str | None = None) -> ClipRequest:
    hc = cfg["hunt"]
    langs = None
    if f.platform != "twitch" or not f.language:   # Twitch filtre déjà la langue à la source
        langs = [str(x).lower() for x in hc.get("require_languages") or []] or None
    return ClipRequest(
        url=url or f.url, credit=preset.get("credit") or (f"@{f.streamer}" if f.streamer else None),
        layout=preset.get("layout"), count=1, raw="chasse", game_hint=f.game,
        min_score=float(hc.get("min_ai_score", 6)), langs=langs,
        meta={"title": f.title, "channel": f.streamer_name, "handle": f"@{f.streamer}" if f.streamer else "",
              "webpage_url": f.url, "platform": f.platform, "views": f.views, "streamer": f.streamer,
              "created": f.created_at.isoformat(), "found_why": ""})


# ---------------------------------------------------------------- Un passage complet

def run_hunt(cfg: dict, streamers: list[dict], base: Path, outdir: Path, *,
             dry_run: bool = False, max_clips: int | None = None) -> int:
    hc = cfg["hunt"]
    if not hc.get("enabled", True):
        log("Chasse désactivée dans config.yaml.")
        return 0
    seen = load_seen()
    found, source_errors = discover(cfg, streamers)
    stats: dict = {}
    ranked = rank(found, cfg, streamers, seen, stats=stats)
    log(f"Chasse : {len(found)} clip(s) trouvés, {len(ranked)} retenus après tri.")
    if stats:
        log("Écartés : " + describe_stats(stats))
    for score, f, _, why in ranked[:12]:
        log(f"  {score:8.1f}  {f.platform:6s} @{f.streamer:<18s} {why} · {f.title[:60]}")
    if dry_run:
        deliver.step_summary(_dry_report(ranked, source_errors, stats))
        return 0

    want = max_clips or int(hc.get("max_per_run", 3))
    budget = int(hc.get("max_candidates", want * 2))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: list[Result] = []
    skipped: list[str] = []
    errors: list[tuple[str, str, str]] = []
    for i, (_, f, preset, why) in enumerate(ranked[:budget]):
        if len(results) >= want:
            break
        work = base / f"hunt{i}"
        made: list[Result] = []
        try:
            req = to_request(f, preset, cfg)
            req.meta["found_why"] = why
            try:
                made = process_request(req, cfg, streamers, work, outdir, i)
            except fetch.FetchError:
                m3u8 = f.extra.get("m3u8")
                if not m3u8:
                    raise
                log("Page du clip illisible, essai direct sur le flux vidéo.")
                made = process_request(to_request(f, preset, cfg, url=m3u8), cfg, streamers, work, outdir, i)
            made = [r for r in made if r.go]
            for res in made:
                deliver_result(res, cfg)
            if not made:
                skipped.append(f"@{f.streamer} · {f.title[:60]} (note trop basse, langue ou contrôle NO-GO)")
            results.extend(made)
        except fetch.FetchError as exc:
            errors.append((f.url, str(exc), exc.hint))
        except Exception as exc:
            errors.append((f.url, f"{type(exc).__name__} : {exc}", ""))
            log(traceback.format_exc())
        finally:
            seen[f.key] = {"d": today, "s": f.streamer, "ok": bool(made)}
            shutil.rmtree(work, ignore_errors=True)
    save_seen(seen)

    report = hunt_report(results, skipped, errors, source_errors)
    deliver.step_summary("## Chasse aux clips\n\n" + report)
    if results:
        # Sans galerie, les anciennes issues restent ouvertes : ce sont les seuls liens vers les clips.
        deliver.open_issue(f"🎬 {len(results)} clip(s) prêt(s) à poster", report, label="chasse",
                           close_previous=bool(deliver.pages_url()))
    deliver.cleanup_releases(int(cfg["delivery"].get("keep_days", 7)))
    return 0


def hunt_report(results: list[Result], skipped: list[str], errors: list, source_errors: list[str]) -> str:
    lines = []
    if deliver.pages_url() and results:
        lines += ["Dans la galerie : **Préparer**, puis **Partager**, puis TikTok, YouTube ou Instagram.", ""]
    lines.append(deliver.markdown_report(results, errors, gallery=True))
    if skipped:
        lines += ["", "<details><summary>Clips écartés</summary>", ""] + [f"- {s}" for s in skipped] + ["", "</details>"]
    if source_errors:
        lines += ["", "Sources illisibles ce passage : " + " ; ".join(source_errors)]
    return "\n".join(lines)


def describe_stats(stats: dict) -> str:
    return ", ".join(f"{n} {why}" for why, n in sorted(stats.items(), key=lambda kv: -kv[1]))


def _dry_report(ranked, source_errors, stats: dict | None = None) -> str:
    lines = ["## Chasse à blanc : ce qui serait monté", ""]
    if stats:
        lines += ["Écartés : " + describe_stats(stats), ""]
    for score, f, _, why in ranked[:15]:
        lines.append(f"- **{score:.0f}** · {f.platform} · @{f.streamer} · {why} · [{f.title[:70] or 'sans titre'}]({f.url})")
    if source_errors:
        lines += ["", "Sources illisibles : " + " ; ".join(source_errors)]
    return "\n".join(lines)
