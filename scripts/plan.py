"""Turn a transcript into an edit plan (what to keep, captions, zooms, hook).

Usage: python scripts/plan.py ".work/my video/transcript.json"
Writes plan.json (Claude reviews and edits this) and transcript.txt (easy to read).
"""
import argparse
import re
from difflib import SequenceMatcher
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (fail, fmt_time, load_audio, load_config, load_json, save_json,  # noqa: E402
                    untranscribed_sounds, workspace)


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def find_fillers(words, fillers):
    single = {f for f in fillers if " " not in f}
    return {i for i, w in enumerate(words) if norm(w["word"]) in single or w["word"] == "(sound)"}


def find_caption_hides(words, crutches, fillers=()):
    """Crutch words ('like,' 'you know,' 'I mean,'). Only when set off by commas, so
    'I like pizza' keeps its 'like'. A comma that only exists because an 'um' follows
    doesn't count ('smelling like, um, garbage' keeps 'like')."""
    hide = set()
    for phrase in crutches:
        parts = phrase.lower().split()
        n = len(parts)
        for i in range(len(words) - n + 1):
            if [norm(words[i + k]["word"]) for k in range(n)] != parts:
                continue
            last = words[i + n - 1]["word"]
            prev = words[i - 1]["word"] if i > 0 else ""
            nxt_is_filler = (i + n) in fillers
            prev_is_filler = (i - 1) in fillers
            if (last.endswith(",") and not nxt_is_filler) or (prev.endswith(",") and not prev_is_filler):
                hide.update(range(i, i + n))
    return hide


def build_keep(words, skip, duration, cfg):
    """Group spoken words into clips, cutting pauses and fillers."""
    max_pause = cfg.get("max_pause", 0.35)
    pad_b = cfg.get("pad_before_word", 0.08)
    pad_a = cfg.get("pad_after_word", 0.15)
    min_len = cfg.get("min_clip_length", 0.4)
    # Cut the breath between sentences even when it's short (jump-cut pacing).
    sent_gap = cfg.get("cut_between_sentences", 0.12)

    groups, cur = [], []
    for i, w in enumerate(words):
        if i in skip:
            if cur:
                groups.append(cur)
            cur = []
            continue
        gap = w["start"] - words[cur[-1]]["end"] if cur else 0
        sentence_end = bool(cur) and re.search(r"[.?!]$", words[cur[-1]]["word"])
        if cur and (gap > max_pause or (sentence_end and gap >= sent_gap)):
            groups.append(cur)
            cur = []
        cur.append(i)
    if cur:
        groups.append(cur)

    keep = []
    for g in groups:
        first, last = g[0], g[-1]
        lo = words[first - 1]["end"] if first > 0 else 0.0
        hi = words[last + 1]["start"] if last + 1 < len(words) else duration
        s = max(words[first]["start"] - pad_b, lo, 0.0)
        e = min(words[last]["end"] + pad_a, hi, duration)
        if e - s < min_len:
            e = min(s + min_len, hi, duration)
        if keep and s <= keep[-1][1]:
            s = keep[-1][1]          # touching lines stay separate clips: every line gets its own cut
        keep.append([round(s, 3), round(e, 3)])
    return keep, groups


def sentence_units(words, groups):
    """Split clips into sentence-ish units: a new unit at every sentence end or clip break."""
    units = []
    for g in groups:
        cur = []
        for i in g:
            cur.append(i)
            if re.search(r"[.?!]$", words[i]["word"]):
                units.append(cur)
                cur = []
        if cur:
            units.append(cur)
    return units


def find_retakes(words, groups):
    """A unit whose words are repeated at the start of one of the next few units is an
    earlier, flubbed take: cut it and keep the later one. Returns [first, last] word ranges."""
    units = sentence_units(words, groups)
    toks = [[norm(words[i]["word"]) for i in u] for u in units]
    cuts = []
    # restarts inside one line: "and if they need to talk about, and if they need to talk to..."
    for u, tk in zip(units, toks):
        i = 0
        while i < len(tk):
            hit = None
            for j in range(i + 3, min(i + 14, len(tk) - 2)):
                if tk[i:i + 3] == tk[j:j + 3]:
                    hit = j
            if hit is not None:
                cuts.append([u[i], u[hit - 1]])
                i = hit
            else:
                i += 1
    # orphan fragments: a lone 1-2 word scrap wedged between two full sentences ("...a second. it.")
    for k in range(1, len(units) - 1):
        u = units[k]
        first = words[u[0]]["word"].lstrip("\"'(")
        if (len(u) <= 2 and words[u[-1]]["end"] - words[u[0]]["start"] < 0.4 and first[:1].islower()
                and re.search(r"[.?!]$", words[units[k - 1][-1]]["word"])):
            cuts.append([u[0], u[-1]])
    for k, a in enumerate(toks):
        if len(a) < 2:
            continue
        for j in range(k + 1, min(k + 4, len(toks))):
            b = toks[j][:len(a) + 2]
            same_start = len(a) >= 3 and a[:3] == toks[j][:3]
            similar = SequenceMatcher(None, a, b).ratio() >= 0.7
            # the earlier take must not be longer than the later one (that'd be a callback, not a flub)
            if (same_start or similar) and len(a) <= len(toks[j]) + 1:
                cuts.append([units[k][0], units[k][-1]])
                break
    return cuts


def make_plan(transcript_path, ws):
    t = load_json(transcript_path)
    style, rules = load_config(ws)
    words, duration = t["words"], t["duration"]
    cut = rules.get("cutting", {})
    # Whisper sometimes stretches one word over a long silence (you looking at notes, a pause).
    # No real word lasts over ~1.2 s: trim it so the silence behind it gets cut.
    words = [dict(w, end=round(w["start"] + 0.6, 3)) if w["end"] - w["start"] > 1.2 else w for w in words]
    # Sounds the transcript missed (mumbled ums etc.) become filler "words" so they get cut.
    try:
        extra = untranscribed_sounds(words, *load_audio(Path(transcript_path).parent, t["source"]))
    except Exception:
        extra = []
    if extra:
        words = sorted(words + [{"word": "(sound)", "start": a, "end": b} for a, b in extra],
                       key=lambda w: w["start"])

    fillers = find_fillers(words, cut.get("filler_words", []))
    crutch = find_caption_hides(words, cut.get("crutch_words", cut.get("remove_fillers_from_captions_only", [])),
                                fillers)
    if cut.get("cut_crutch_words", True):
        fillers |= crutch       # cut from the video, not just hidden
        hides = set()
    else:
        hides = crutch
    keep, groups = build_keep(words, fillers, duration, cut)
    retakes = find_retakes(words, groups)
    cut_words = {i for a, b in retakes for i in range(a, b + 1)}

    src = Path(t["source"])
    plan = {
        "_help": ("Edit this, then run render.py. All times are seconds in the ORIGINAL "
                  "video. Word numbers match transcript.txt."),
        "source": str(src),
        "output": str(ws / "Finished" / f"{src.stem} - edited.mp4"),
        "duration": duration,
        "keep": keep,
        "remove_words": retakes,
        "remove": [],
        "hook": {"text": "", "seconds": style.get("hook", {}).get("seconds_on_screen", 3.0)},
        "callouts": [],
        "extra_zooms": [],
        "music": None,
        "caption_hide": sorted(hides),
        "words": [dict(w, n=i) for i, w in enumerate(words)],
    }
    out_dir = Path(transcript_path).parent
    save_json(out_dir / "plan.json", plan)

    lines = [f"TRANSCRIPT  {src.name}  ({fmt_time(duration)})",
             "Each line is one clip the editor is keeping. [wA-wB] = word numbers.",
             "(um) = filler or crutch word that will be cut.  {like} = hidden from captions only.",
             "[[...]] = an earlier take of a line you said again: auto-cut (the later take is kept).",
             "  Undo by deleting that pair from remove_words in plan.json.", ""]
    for k, g in enumerate(groups):
        parts = []
        for i in range(g[0], g[-1] + 1):
            w = words[i]["word"]
            if i in cut_words and (i == g[0] or i - 1 not in cut_words):
                parts.append("[[")
            parts.append(f"({w})" if i in fillers else f"{{{w}}}" if i in hides else w)
            if i in cut_words and i + 1 not in cut_words:
                parts.append("]]")
        flag = ""
        lines.append(f"[w{g[0]}-w{g[-1]}] {fmt_time(words[g[0]]['start'])}  "
                     f"{' '.join(parts)}{flag}")
    if fillers:
        lines += ["", "Fillers cut: " + ", ".join(f"w{i} '{words[i]['word']}'"
                                                  for i in sorted(fillers))]
    kept = sum(e - s for s, e in keep) - sum(words[b]["end"] - words[a]["start"] for a, b in retakes)
    lines += ["", f"Original: {fmt_time(duration)}   After cuts: {fmt_time(kept)}"]
    (out_dir / "transcript.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"Plan saved: {out_dir / 'plan.json'}")
    print(f"Readable transcript: {out_dir / 'transcript.txt'}")
    print(f"{len(keep)} clips, {len(fillers)} fillers cut, {len(retakes)} retakes cut. "
          f"{fmt_time(duration)} -> {fmt_time(kept)}")
    return out_dir / "plan.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    if not Path(a.transcript).exists():
        fail(f"Can't find {a.transcript}")
    make_plan(a.transcript, workspace(a.workspace))


if __name__ == "__main__":
    main()
