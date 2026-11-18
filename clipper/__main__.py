"""Ligne de commande.

    python -m clipper run                    # lit la demande dans la variable REQUEST_TEXT
    python -m clipper run --request "LIEN 12:30-13:10 @streamer"
    python -m clipper local video.mp4 --options "0:40-1:10 @streamer #crop"
    python -m clipper hunt [--dry-run] [--max 3]   # chasse aux clips du moment
    python -m clipper gallery --out site           # page de la galerie
    python -m clipper doctor --request "LIEN"   # bilan de santé
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from .config import load_config, load_streamers
from .util import log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipper", description="Transforme une source vidéo en Shorts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="traite une demande en texte libre")
    p_run.add_argument("--request", default=None)
    p_local = sub.add_parser("local", help="traite un fichier déjà présent sur la machine")
    p_local.add_argument("file")
    p_local.add_argument("--options", default="", help='ex. "0:40-1:10 @streamer #crop x2"')
    p_radar = sub.add_parser("hunt", aliases=["radar"], help="chasse : trouve, monte et range les clips du moment")
    p_radar.add_argument("--dry-run", action="store_true")
    p_radar.add_argument("--max", type=int, default=None)
    p_gal = sub.add_parser("gallery", help="construit la page de la galerie")
    p_gal.add_argument("--from-dir", default=None, help="fiches locales au lieu des releases (essais)")
    p_doc = sub.add_parser("doctor", help="bilan de santé : clés, services, liens")
    p_doc.add_argument("--request", default=None)
    for p in (p_run, p_local, p_radar, p_doc, p_gal):
        p.add_argument("--out", default="out")
        p.add_argument("--work", default=None)
    args = ap.parse_args(argv)

    cfg, streamers = load_config(), load_streamers()
    outdir = Path(args.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    base = Path(args.work).resolve() if args.work else Path(tempfile.mkdtemp(prefix="clipper-"))
    base.mkdir(parents=True, exist_ok=True)

    if args.cmd == "run":
        from .learn import is_learn_request, run_learn
        from .pipeline import run_text
        text = args.request if args.request is not None else os.environ.get("REQUEST_TEXT", "")
        if is_learn_request(text):
            return run_learn(text, cfg, base)
        return run_text(text, cfg, streamers, base, outdir)

    if args.cmd == "local":
        from .pipeline import run_requests
        from .request import parse_requests
        src = Path(args.file).resolve()
        if not src.exists():
            log(f"Fichier introuvable : {src}")
            return 1
        reqs = parse_requests("https://local.invalid/x " + args.options)
        reqs[0].url = str(src)
        results, errors = run_requests(reqs[:1], cfg, streamers, base, outdir)
        for res in results:
            log(f"\n{'GO' if res.go else 'NO-GO'} · {res.path}\nTitre : {res.title}\n"
                f"Accroche : {res.hook}\nMontage : {res.facts}\nProblèmes : {res.problems}\n"
                f"Remarques : {res.notes}\n\n{res.description}")
        for url, message, hint in errors:
            log(f"ÉCHEC {url} : {message} {hint}")
        return 0 if results else 1

    if args.cmd in ("hunt", "radar"):
        from .hunt import run_hunt
        return run_hunt(cfg, streamers, base, outdir, dry_run=args.dry_run, max_clips=args.max)

    if args.cmd == "gallery":
        from .gallery import build
        build(outdir, cfg, Path(args.from_dir).resolve() if args.from_dir else None)
        return 0

    if args.cmd == "doctor":
        from .doctor import run_doctor
        text = args.request if args.request is not None else os.environ.get("REQUEST_TEXT", "")
        return run_doctor(text, cfg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
