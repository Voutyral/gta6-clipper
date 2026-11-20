"""Le chasseur : trouve les clips du moment, garde les meilleurs, les monte et les range.

À chaque passage :
1. lecture des clips les plus regardés des dernières 24 h (Twitch, Kick, streamers suivis) ;
2. tri par vitesse de vues (un clip récent qui décolle passe devant un vieux clip à succès) ;
3. montage des meilleurs, avec une note de l'IA qui écarte les moments faibles ;
4. galerie mise à jour et issue ouverte : le téléphone reçoit une notification.
"""
from __future__ import annotations

import json
import re
import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import campaign, deliver, fetch, sources
from .config import ROOT, match_streamer
from .pipeline import Result, deliver_result, process_request
from .request import ClipRequest
from .sources import Found, discover
from .transcribe import guess_language
from .util import game_key, log

SEEN = ROOT / "state" / "seen.json"
# Clips déjà faits à refaire avec le montage corrigé (sous-titres, gros mots…) : la chasse les
# passe en premier, même déjà vus, puis les retire de la liste. Format : [{"url", "streamer", "title"}].
REDO = ROOT / "state" / "redo.json"
REDO_PER_RUN = 12   # un passage dure au plus 90 min : environ 2 min par clip


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


def load_redo() -> list[dict]:
    try:
        data = json.loads(REDO.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict) and d.get("url")] if isinstance(data, list) else []


def save_redo(items: list[dict]) -> None:
    if items:
        REDO.parent.mkdir(exist_ok=True)
        REDO.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    elif REDO.exists():
        REDO.unlink()


def redo_found(item: dict, found: list[Found]) -> Found:
    """Le clip à refaire tel que la chasse vient de le trouver, sinon relu sur Kick, sinon rebâti
    depuis son lien (le montage relit la page du clip de toute façon)."""
    url = str(item["url"])
    for f in found:
        if f.url == url:
            return f
    m = re.search(r"kick\.com/([^/?#]+)/clips/([\w-]+)", url)
    if m:
        got = sources.kick_clip(m.group(2))
        if got:
            return got
    slug = m.group(1) if m else str(item.get("streamer") or "")
    cid = m.group(2) if m else url.rstrip("/").rsplit("/", 1)[-1]
    return Found(platform="kick" if "kick.com" in url else "twitch", id=cid, url=url,
                 title=str(item.get("title") or ""), views=int(item.get("views") or 0), duration=0.0,
                 created_at=datetime.now(timezone.utc), streamer=slug, streamer_name=slug,
                 game=str(item.get("game") or ""))


def made_today_total(seen: dict, streamers: list[dict] | None = None) -> int:
    """Clips montés aujourd'hui (UTC) hors streamers en campagne (ceux-là ont leur propre rythme)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    paid = {str(s.get(k, "")).lower().lstrip("@") for s in streamers or [] if campaign.of(s)
            for k in ("kick", "twitch", "youtube", "name")} - {""}
    return sum(1 for v in seen.values()
               if v.get("ok") and v.get("d") == today and str(v.get("s", "")).lower() not in paid)


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
    boosts = {game_key(k): float(v) for k, v in (cfg["hunt"].get("game_boost") or {}).items()}

    def drop(reason: str) -> None:
        stats[reason] = stats.get(reason, 0) + 1

    hc = cfg["hunt"]
    now = now or datetime.now(timezone.utc)
    blocked = {str(b).lower().lstrip("@") for b in hc.get("blocked_streamers") or []}
    excluded = [str(w).lower() for w in hc.get("exclude_words") or [] if str(w).strip()]
    wanted_langs = [str(x).lower() for x in hc.get("require_languages") or [] if str(x).strip()]
    lives: dict[str, set[str]] = {}   # lives autorisés par campagne, par streamer
    out = []
    for f in found:
        preset = match_streamer(streamers, f.streamer, f.streamer_name)
        camp = campaign.of(preset)
        if f.key in seen:
            drop("déjà vus")
            continue
        if f.streamer.lower() in blocked:
            drop("streamer bloqué")
            continue
        if camp:
            # Campagne : seule la source autorisée compte (bon jour, bon live, bon jeu).
            if campaign.ended(camp, now):
                drop("campagne terminée")
                continue
            key = f.streamer.lower()
            if key not in lives:
                lives[key] = campaign.stream_ids(found, camp, key)
            ok, reason = campaign.footage_ok(f, camp, lives[key])
            if not ok:
                drop(reason)
                continue
        vod = bool((f.extra or {}).get("vod"))
        if vod and (f.extra or {}).get("live"):
            drop("live encore en cours (la rediffusion viendra après)")
            continue
        if vod and 0 < f.duration < 60:
            drop("rediffusion trop courte")
            continue
        if not vod and not float(hc.get("min_duration_s", 8)) <= f.duration <= float(hc.get("max_duration_s", 120)):
            drop("durée hors limites")
            continue
        if hc.get("allowlist_only") and not preset:
            drop("hors streamers.yaml")
            continue
        # Vues : `min_views` est le seuil voulu. Les jours calmes, la chasse descend jusqu'au
        # plancher `min_views_floor`, mais ces clips passent après ceux qui dépassent le seuil.
        wanted_views = int(preset.get("min_views", hc.get("min_views", 150)))
        floor = min(wanted_views, int(hc.get("min_views_floor", wanted_views)))
        if f.views < floor:
            drop("pas assez de vues")
            continue
        if not camp and f.age_hours(now) > float(hc.get("max_age_hours", 30)):
            drop("trop anciens")   # une campagne garde son stream jusqu'à la date limite
            continue
        title = f.title.lower()
        if not preset.get("story_ok") and any(w in title for w in excluded):
            drop("mot exclu dans le titre")
            continue
        # Vues par heure, avec un amortissement pour ne pas surestimer les clips de dix minutes.
        score = f.views / (f.age_hours(now) + 2.0) ** 0.8
        why = f"{f.views} vues en {f.age_hours(now):.0f} h"
        if f.views < wanted_views:
            why += ", peu de vues"
        boost = boosts.get(game_key(f.game), 1.0)
        if boost != 1.0:
            score *= boost
            why += f", {f.game} prioritaire"
        if camp:
            why += f", campagne « {camp.get('name') or preset.get('name')} »"
            if vod:
                why += ", rediffusion : l'outil y cherche lui-même les moments forts"
        elif preset:
            score *= float(hc.get("allowlist_boost", 2.0))
            why += ", streamer suivi"
        elif wanted_langs and not f.language:
            # Kick ne donne pas la langue : un titre clairement dans une autre langue suffit à écarter,
            # sinon elle est vérifiée après téléchargement (Gemini écoute le son).
            guess = guess_language([{"raw": w} for w in f.title.split()])
            if guess and guess not in wanted_langs:
                drop("titre dans une autre langue")
                continue
            why += ", langue à vérifier"
        elif wanted_langs and f.language not in wanted_langs:
            drop("autre langue")
            continue
        out.append((score, f, preset, why))
    # Campagnes rémunérées d'abord, puis langue connue, puis les clips au dessus du seuil de vues,
    # puis la vitesse.
    out.sort(key=lambda t: (not campaign.of(t[2]), bool((t[1].extra or {}).get("vod")),
                            "langue à vérifier" in t[3], "peu de vues" in t[3], -t[0]))
    if hc.get("mix_languages", True) and len(wanted_langs) > 1:
        out = mix_languages(out, wanted_langs)
    unknown_cap = int(hc.get("max_unknown_language", 1))
    picked, count, unknown = [], {}, 0
    for item in out:
        s = item[1].streamer.lower()
        cap = int(item[2].get("max_per_day", hc.get("max_per_streamer_day", 2)))
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


def clip_language(f: Found, wanted: list[str]) -> str:
    """Langue d'un clip : donnée par Twitch, devinée sur le titre pour Kick, « ? » si inconnue."""
    if f.language:
        return f.language
    guess = guess_language([{"raw": w} for w in f.title.split()])
    return guess if guess in wanted else "?"


def mix_languages(items: list, wanted: list[str]) -> list:
    """Alterne les langues (un clip anglais, un français…) à qualité égale.

    Les campagnes restent en tête ; ensuite chaque palier (vues suffisantes, peu de vues,
    langue à vérifier) est alterné à part, pour qu'un clip faible ne passe pas devant un bon.
    """
    head = [t for t in items if campaign.of(t[2])]
    tiers: dict[int, list] = {}
    for t in items:
        if not campaign.of(t[2]):
            tier = 2 if "langue à vérifier" in t[3] else (1 if "peu de vues" in t[3] else 0)
            tiers.setdefault(tier, []).append(t)
    out = list(head)
    for tier in sorted(tiers):
        groups: dict[str, list] = {}
        for t in tiers[tier]:   # ordre des groupes : celui du meilleur clip de chaque langue
            groups.setdefault(clip_language(t[1], wanted), []).append(t)
        queues = list(groups.values())
        while any(queues):
            for q in queues:
                if q:
                    out.append(q.pop(0))
    return out


def to_request(f: Found, preset: dict, cfg: dict, url: str | None = None) -> ClipRequest:
    hc = cfg["hunt"]
    langs = None
    spoken = campaign.language(preset)
    if spoken:                                      # langue du streamer, donnée dans streamers.yaml
        langs = [spoken]
    elif f.platform != "twitch" or not f.language:   # Twitch filtre déjà la langue à la source
        langs = [str(x).lower() for x in hc.get("require_languages") or []] or None
    vod = bool((f.extra or {}).get("vod"))
    count = int(campaign.of(preset).get("vod_clips", 4)) if vod else 1
    return ClipRequest(
        url=url or f.url, credit=preset.get("credit") or (f"@{f.streamer}" if f.streamer else None),
        layout=preset.get("layout"), count=count, raw="chasse", game_hint=f.game, lang=spoken or None,
        min_score=float(preset.get("min_ai_score", hc.get("min_ai_score", 6))), langs=langs,
        meta={"title": f.title, "channel": f.streamer_name, "handle": f"@{f.streamer}" if f.streamer else "",
              "webpage_url": f.url, "platform": f.platform, "views": f.views, "streamer": f.streamer,
              "created": f.created_at.isoformat(), "found_why": "",
              "duration": f.duration if vod and f.duration else None})


# ---------------------------------------------------------------- Un passage complet

def run_hunt(cfg: dict, streamers: list[dict], base: Path, outdir: Path, *,
             dry_run: bool = False, max_clips: int | None = None, campaign_only: bool = False) -> int:
    hc = cfg["hunt"]
    if not hc.get("enabled", True):
        log("Chasse désactivée dans config.yaml.")
        return 0
    seen = load_seen()
    if campaign_only:   # passages en plus d'un jour de campagne : uniquement les streamers en campagne
        streamers = [s for s in streamers if campaign.of(s) and not campaign.ended(campaign.of(s))]
        if not streamers:
            log("Passage réservé aux campagnes, mais aucune campagne en cours dans streamers.yaml.")
            return 0
        cfg = dict(cfg, hunt=dict(hc, twitch=dict(hc.get("twitch") or {}, enabled=False),
                                  kick=dict(hc.get("kick") or {}, enabled=False)))
        hc = cfg["hunt"]
    found, source_errors = discover(cfg, streamers)
    stats: dict = {}
    ranked = rank(found, cfg, streamers, seen, stats=stats)
    if campaign_only:
        ranked = [r for r in ranked if campaign.of(r[2])]
    # Clips à refaire (montage corrigé) : en tête de file, même déjà vus. Les vérifications de la
    # campagne (bon stream, bon jeu) ont été faites au premier montage.
    redo = load_redo()
    redo_jobs, origin = [], {}   # origin : clip à refaire → lien noté dans redo.json
    for item in redo[:REDO_PER_RUN]:
        f = redo_found(item, found)
        preset = match_streamer(streamers, f.streamer, f.streamer_name)
        if campaign_only and not campaign.of(preset):
            continue
        why = "à refaire avec le montage corrigé" + (f", campagne « {campaign.of(preset).get('name')} »"
                                                      if campaign.of(preset) else "")
        redo_jobs.append((0.0, f, preset, why))
        origin[id(f)] = str(item["url"])
    if redo_jobs:
        urls = {j[1].url for j in redo_jobs}
        ranked = redo_jobs + [r for r in ranked if r[1].url not in urls]
        log(f"À refaire : {len(redo_jobs)} clip(s) en tête de file, {max(0, len(redo) - len(redo_jobs))} après.")
    log(f"Chasse : {len(found)} clip(s) trouvés, {len(ranked)} retenus après tri.")
    if stats:
        log("Écartés : " + describe_stats(stats))
    for score, f, _, why in ranked[:12]:
        log(f"  {score:8.1f}  {f.platform:6s} @{f.streamer:<18s} {why} · {f.title[:60]}")
    if dry_run:
        deliver.step_summary(_dry_report(ranked, source_errors, stats))
        return 0

    # Deux quotas par passage : les clips de campagne ne prennent jamais la place des autres
    # streamers. Le 19/11 au soir, Dom (en direct, des centaines de clips Kick) a pris toutes les
    # places pendant 8 h : 100 clips Dom, aucun autre. Les clips à refaire passent tous.
    hc_candidates = int(hc.get("max_candidates", 16))
    camp_q = [r for r in ranked if campaign.of(r[2])]
    free_q = [r for r in ranked if not campaign.of(r[2])]
    n_redo_camp = sum(1 for j in redo_jobs if campaign.of(j[2]))
    want = {True: 0, False: 0}   # True : campagne, False : hors campagne
    if max_clips:
        queue = ranked[:max(hc_candidates, max_clips * 2)]   # nombre demandé à la main : un seul total
    else:
        if camp_q:
            want[True] = max(int(hc.get("campaign_max_per_run", hc.get("max_per_run", 3))), n_redo_camp)
        if not campaign_only:
            want[False] = max(int(hc.get("max_per_run", 3)), len(redo_jobs) - n_redo_camp)
        queue = (camp_q[:max(hc_candidates, want[True] * 2)] if want[True] else []) + \
                (free_q[:max(hc_candidates, want[False] * 2)] if want[False] else [])
    # Plafond du jour pour les clips hors campagne : la chasse passe souvent, la galerie reste lisible.
    day_cap = int(hc.get("max_per_day", 0) or 0)
    free_left = None
    if day_cap and not max_clips:
        free_left = max(0, day_cap - made_today_total(seen, streamers))
        if free_left == 0:
            log(f"Plafond du jour atteint ({day_cap} clips) : seuls les clips de campagne sont montés.")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results: list[Result] = []
    made_n = {True: 0, False: 0}
    skipped: list[str] = []
    errors: list[tuple[str, str, str]] = []
    attempted: set[str] = set()
    for i, (_, f, preset, why) in enumerate(queue):
        camp = bool(campaign.of(preset))
        if max_clips:
            if len(results) >= max_clips:
                break
        elif made_n[True] >= want[True] and made_n[False] >= want[False]:
            break
        elif made_n[camp] >= want[camp]:
            continue
        if free_left is not None and not camp and free_left <= 0:
            continue
        attempted.add(origin.get(id(f), f.url))
        work = base / f"hunt{i}"
        made: list[Result] = []
        retry = False
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
            if free_left is not None and not camp:
                free_left -= len(made)
            made_n[camp] += len(made)
            results.extend(made)
        except fetch.FetchError as exc:
            errors.append((f.url, str(exc), exc.hint))
            retry = bool((f.extra or {}).get("vod"))
        except Exception as exc:
            errors.append((f.url, f"{type(exc).__name__} : {exc}", ""))
            log(traceback.format_exc())
        finally:
            if not retry:
                seen[f.key] = {"d": today, "s": f.streamer, "ok": bool(made)}
            shutil.rmtree(work, ignore_errors=True)
    save_seen(seen)
    if redo_jobs:
        save_redo([d for d in redo if str(d["url"]) not in attempted])

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
