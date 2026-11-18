"""Sous-titres et habillage texte au format ASS (rendu par libass dans ffmpeg).

Zones sûres d'un Short 1080x1920 : rien d'important dans les 150 px du haut,
les 400 px du bas (titre, chaîne) ni les 150 px de droite (boutons).
"""
from __future__ import annotations

import re

W, H = 1080, 1920
WHITE = "&H00FFFFFF"
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]+")
_EDGE = " \t\n.,;:…\"«»()[]{}–—"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Poppins ExtraBold,124,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,9,4,2,60,60,455,1
Style: Hook,Anton,150,&H00101010,&H00101010,{hook_box},&H00000000,0,0,0,0,100,100,1,0,3,16,0,8,60,60,190,1
Style: Ctx,Poppins ExtraBold,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,5,2,8,90,90,0,1
Style: Credit,Poppins ExtraBold,46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,4,1,2,60,60,390,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def strip_emoji(text: str) -> str:
    """libass n'affiche pas les emojis en couleur : on les retire du texte incrusté."""
    return EMOJI_RE.sub("", text or "").strip()


def display_word(raw: str) -> str:
    word = strip_emoji(raw).strip(_EDGE).strip("-").upper()
    return word if any(ch.isalnum() for ch in word) else ""


def normalize_words(words: list[dict], dur: float) -> list[dict]:
    """Nettoie les mots bruts de la transcription et garantit des temps croissants."""
    out = []
    for w in words:
        disp = display_word(w.get("raw", ""))
        if not disp:
            continue
        s = min(max(0.0, float(w["s"])), dur)
        e = min(max(s + 0.04, float(w["e"])), dur + 0.3)
        out.append({"raw": w["raw"], "w": disp, "s": s, "e": e, "p": float(w.get("p", 1.0)),
                    "eos": w["raw"].rstrip()[-1:] in ".!?…"})
    for i in range(1, len(out)):
        if out[i]["s"] < out[i - 1]["s"]:
            out[i]["s"] = out[i - 1]["s"]
            out[i]["e"] = max(out[i]["e"], out[i]["s"] + 0.04)
        if out[i - 1]["e"] > out[i]["s"]:
            out[i - 1]["e"] = max(out[i - 1]["s"] + 0.03, out[i]["s"])
    return out


def group_words(words: list[dict], max_words: int = 3, max_chars: int = 14,
                max_gap: float = 0.7) -> list[list[dict]]:
    """Petits groupes lisibles d'un coup d'œil, coupés aux silences et aux fins de phrase."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            chars = sum(len(x["w"]) for x in cur) + len(cur) + len(w["w"])
            if (len(cur) >= max_words or chars > max_chars or w["s"] - cur[-1]["e"] > max_gap
                    or cur[-1].get("eos")):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def wrap(text: str, width: int, max_lines: int = 3) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def _caption_events(words: list[dict], cfg: dict) -> list[str]:
    cap = cfg["captions"]
    hl = cap.get("highlight", "&H0000E5FF")
    reveal = str(cap.get("style", "reveal")).lower() == "reveal"
    groups = group_words(words, int(cap.get("max_words", 3)), int(cap.get("max_chars", 14)))
    events = []
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else None
        g_end = g[-1]["e"] + 0.30
        if nxt is not None:
            g_end = min(g_end, nxt)
        g_end = max(g_end, g[-1]["s"] + 0.08)
        for i, w in enumerate(g):
            t0 = w["s"]
            t1 = g[i + 1]["s"] if i + 1 < len(g) else g_end
            if t1 - t0 < 0.02:
                continue
            parts = []
            for k, x in enumerate(g):
                txt = esc(x["w"])
                if k == i:
                    parts.append("{\\c" + hl + "}" + txt + "{\\c" + WHITE + "}")
                elif k > i and reveal:
                    parts.append("{\\alpha&H99&}" + txt + "{\\alpha&H00&}")
                else:
                    parts.append(txt)
            pop = "{\\fscx92\\fscy92\\t(0,80,\\fscx100\\fscy100)}" if i == 0 else ""
            events.append(f"Dialogue: 2,{ass_time(t0)},{ass_time(t1)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    return events


def build_ass(words: list[dict], *, hook: str, context: str, credit: str, total: float,
              cfg: dict, hook_until: float | None = None,
              hook_pos: tuple[int, int] | None = None, cta: str = "", cta_s: float = 2.8) -> str:
    """Fichier ASS complet : accroche, ligne de contexte, crédit, sous-titres mot à mot.

    `hook_pos` centre l'accroche ailleurs qu'en haut (à la jointure en mise en page « split »).
    `cta` : appel à l'action d'une campagne (« SUBSCRIBE ON YOUTUBE »), affiché les dernières secondes.
    """
    ed = cfg["edit"]
    lines = [HEADER.format(hook_box=cfg["captions"].get("highlight", "&H0000E5FF"))]
    hook_end = min(total, hook_until if hook_until is not None else float(ed.get("hook_seconds", 3.5)))
    hook_lines = wrap(esc(strip_emoji(hook).upper()), 18, 2) if hook else []
    top = 190
    if hook_lines:
        place = ""
        if hook_pos:
            place = f"\\an5\\pos({hook_pos[0]},{hook_pos[1]})"
            top = hook_pos[1] - len(hook_lines) * 82 + 16
        lines.append(f"Dialogue: 5,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,"
                     "{\\fad(100,180)" + place + "}" + "\\N".join(hook_lines))
    ctx = esc(strip_emoji(context)) if context else ""
    if ctx:
        y = top - 16 + len(hook_lines) * 163 + 22
        ctx_end = min(total, hook_end + 1.5)
        lines.append(f"Dialogue: 4,{ass_time(0.25)},{ass_time(ctx_end)},Ctx,,0,0,0,,"
                     "{\\fad(150,200)\\an8\\pos(540," + str(y) + ")}" + "\\N".join(wrap(ctx, 30, 2)))
    if credit:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(total)},Credit,,0,0,0,,"
                     "{\\alpha&H30&}" + esc(strip_emoji(credit)))
    cta_lines = wrap(esc(strip_emoji(cta).upper()), 18, 3) if cta else []
    if cta_lines and total > hook_end + 1.0:
        start = max(hook_end + 0.5, total - cta_s)
        lines.append(f"Dialogue: 6,{ass_time(start)},{ass_time(total)},Hook,,0,0,0,,"
                     "{\\fad(150,0)\\an5\\pos(540,860)}" + "\\N".join(cta_lines))
    lines.extend(_caption_events(words, cfg))
    return "\n".join(lines) + "\n"
