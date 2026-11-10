"""Render the finished video from plan.json.

Usage: python scripts/render.py ".work/my video/plan.json" [--preview]

What it does:
  1. Finds your face through the video (cached).
  2. Cuts every clip straight from your original file, punching in on your face
     from full resolution (sharp, even from 4K), with tiny audio fades so cuts never click.
  3. Places captions just under your chin and inside the Instagram safe zone,
     the hook as a static title from the first frame, callouts where they fit.
  4. Mixes voice, sound effects and music, and exports to Finished/.
  5. Checks its own work: finds your face in the FINAL video and confirms no text
     covers it; saves cuts.jpg (both sides of every cut) and check.jpg.
"""
import argparse
import math
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (ASSETS, edge_levels, fail, fmt_time, load_audio, load_config,  # noqa: E402
                    load_json, probe, require_ffmpeg, run, snap_to_silence, workspace)
import faces as facemod  # noqa: E402
import hooks  # noqa: E402

SAFE_BOTTOM = 430   # px at the bottom that Instagram's caption/username/audio covers
SAFE_TOP = 200      # px at the top (status bar, "Reels" header)
SIDE = 110          # side margins (right side buttons sit over ~x>950)
TOP_SIDE = 70       # top titles can go wider: the buttons only start halfway down
RIGHT_BUTTONS = 150  # keep chest text this far from the right edge (like, comment, share)


# ---------- timeline ----------

def subtract(ranges, cuts):
    out = []
    for s, e in ranges:
        pieces = [[s, e]]
        for cs, ce in cuts:
            nxt = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:
                    nxt.append([ps, pe])
                    continue
                if cs > ps:
                    nxt.append([ps, cs])
                if ce < pe:
                    nxt.append([ce, pe])
            pieces = nxt
        out += pieces
    return out


def final_keep(plan, fps, audio=None):
    words = plan["words"]
    cuts = [list(r) for r in plan.get("remove", [])]
    for a, b in plan.get("remove_words", []):
        a, b = sorted((int(a), int(b)))
        cuts.append([words[a]["start"], words[b]["end"]])
    keep = subtract([list(r) for r in plan["keep"]], cuts)

    # Drop leftover slivers (padding around removed words) that hold no speech.
    def mid(w):
        return (w["start"] + w["end"]) / 2
    spoken = [mid(w) for w in words if not any(cs <= mid(w) < ce for cs, ce in cuts)]
    keep = [[s, e] for s, e in keep if any(s <= m < e for m in spoken)]
    if audio is not None:
        kept_words = [w for w in words if not any(cs <= mid(w) < ce for cs, ce in cuts)]
        keep = snap_to_silence(keep, words, *audio)
        keep = [[s, e] for s, e in keep if any(s <= mid(w) < e for w in kept_words)]
    # A clip shorter than 0.8 s with a tiny gap next to it just looks like a glitch: join it.
    def ends_line(t):
        before = [w for w in words if w["end"] <= t + 0.05]
        return bool(before) and bool(re.search(r"[.?!]$", before[-1]["word"]))
    joined = []
    for s, e in keep:
        if (joined and s - joined[-1][1] < 0.2 and not ends_line(joined[-1][1])
                and (e - s < 0.8 or joined[-1][1] - joined[-1][0] < 0.8)):
            joined[-1][1] = e
        else:
            joined.append([s, e])
    keep = joined

    snapped = []
    for s, e in sorted(keep):
        s, e = round(s * fps) / fps, round(e * fps) / fps   # frame-accurate, no drift
        if e - s >= 2 / fps:
            if snapped and s < snapped[-1][1]:
                s = snapped[-1][1]
            if e - s >= 2 / fps:
                snapped.append([s, e])
    if not snapped:
        fail("The plan removes everything. Check 'keep', 'remove' and 'remove_words'.")
    return snapped


class Timeline:
    def __init__(self, keep):
        self.keep, self.offsets, t = keep, [], 0.0
        for s, e in keep:
            self.offsets.append(t)
            t += e - s
        self.total = t

    def contains(self, t):
        return any(s <= t < e for s, e in self.keep)

    def map(self, t):
        """Original time -> finished-video time (snaps forward into the next clip)."""
        for (s, e), off in zip(self.keep, self.offsets):
            if t < s:
                return off
            if t < e:
                return off + (t - s)
        return self.total


# ---------- framing ----------

def even(x):
    return int(round(x / 2) * 2)


def framing(box, zoom, sw, sh, W, H):
    """Crop window in the source for one clip, plus where the face lands on screen."""
    r = W / H
    cw0 = min(sw, sh * r)
    ch0 = cw0 / r
    cw, ch = cw0 / zoom, ch0 / zoom
    if box:
        fx = (box[0] + box[2] / 2) * sw
        fy = (box[1] + box[3] / 2) * sh
        y0b = (sh - ch0) / 2
        frac = min(max((fy - y0b) / ch0, 0.2), 0.8)   # keep the face at the same height on screen
        y0 = fy - frac * ch
        x0 = fx - cw / 2
    else:
        x0, y0 = (sw - cw) / 2, (sh - ch) / 2
    x0 = min(max(x0, 0), sw - cw)
    y0 = min(max(y0, 0), sh - ch)
    crop = [even(cw), even(ch), even(x0), even(y0)]
    crop[2] = min(crop[2], even(sw - crop[0]) if sw - crop[0] >= 0 else 0)
    crop[3] = min(crop[3], even(sh - crop[1]) if sh - crop[1] >= 0 else 0)
    face = None
    if box:
        def to_out(px, py):
            return ((px - crop[2]) / crop[0] * W, (py - crop[3]) / crop[1] * H)
        x1, y1 = to_out(box[0] * sw, box[1] * sh)
        x2, y2 = to_out((box[0] + box[2]) * sw, (box[1] + box[3]) * sh)
        head_top = y1 - (y2 - y1) * 0.45     # hair / cap above the detected face
        face = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "chin": y2, "head_top": head_top}
    return crop, face


# ---------- text layout ----------

def char_w(size, upper):
    return size * (0.66 if upper else 0.56)


def text_box(text, size, upper, W, pad=0):
    w = min(len(text) * char_w(size, upper) + pad * 2, W - 2 * SIDE)
    lines = max(1, math.ceil((len(text) * char_w(size, upper)) / (W - 2 * SIDE - pad * 2)))
    h = lines * size * 1.22 + pad * 2
    return w, h, lines


def ass_color(hex_color, alpha=0):
    h = (hex_color or "#000000").lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ass_time(t):
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_text(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_chunks(words, n_max, max_chars):
    chunks, cur = [], []
    for w in words:
        length = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
        if cur and (len(cur) >= n_max or length > max_chars or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["word"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def clip_windows(start, end, blocks):
    """Cut [start, end) around blocked windows; returns the visible pieces."""
    pieces = [[start, end]]
    for bs, be in blocks:
        nxt = []
        for s, e in pieces:
            if be <= s or bs >= e:
                nxt.append([s, e])
                continue
            if bs > s:
                nxt.append([s, bs])
            if be < e:
                nxt.append([be, e])
        pieces = nxt
    return [p for p in pieces if p[1] - p[0] > 0.04]


def build_layout(plan, tl, style, W, H, clip_faces, work=None, ws=None):
    """Decide where every piece of text goes. Returns (ass_text, layout_boxes, notes)."""
    cap, hook, call = style["captions"], style["hook"], style["callouts"]
    notes = []
    cap_upper = bool(cap.get("uppercase"))
    boxed = bool(cap.get("box_color"))
    pad = cap.get("outline_width", 0) if boxed else 0
    cap_h = cap["size"] * 1.22 + pad * 2
    max_cap_top = H - SAFE_BOTTOM - cap_h

    # Captions: one fixed line just under the lowest chin in the video.
    chins = [f["chin"] for f in clip_faces if f]
    if cap.get("position", "under_chin") == "under_chin" and chins:
        cap_top = max(chins) + cap.get("gap_below_chin", 40)
    elif isinstance(cap.get("position"), (int, float)):
        cap_top = H - cap["position"] - cap_h
    else:
        cap_top = H * 0.62
    if cap_top > max_cap_top:
        notes.append("Your chin sits low in the frame, so captions are as low as the safe zone "
                     "allows. Film with a little more headroom below your face next time.")
        cap_top = max_cap_top
    cap_top = max(cap_top, H * 0.45)

    def highest_face(s, e):
        tops = [f["y1"] + (f["y2"] - f["y1"]) * 0.22 for f in clip_faces
                if f and f["o_s"] < e and f["o_e"] > s]
        return min(tops) if tops else H * 0.3

    def fits_top(h_text, top, s, e):
        return top + h_text <= highest_face(s, e) - 24

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
             "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
             "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
             "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]

    def style_line(name, s, box_pad_default):
        has_box = bool(s.get("box_color"))
        border = 3 if has_box else 1
        oc = s.get("box_color") if has_box else s.get("outline_color", "#000000")
        outline = s.get("outline_width", box_pad_default if has_box else 6)
        back = ass_color(s.get("box_color") or s.get("outline_color", "#000000"), 0 if has_box else 0x70)
        return (f"Style: {name},{s.get('font', cap['font'])},{s['size']},{ass_color(s['text_color'])},"
                f"{ass_color(s['text_color'])},{ass_color(oc)},{back},-1,0,0,0,100,100,0,0,"
                f"{border},{outline},{s.get('shadow', 0)},8,{SIDE},{SIDE},0,1")

    lines += [style_line("Caption", cap, 14), style_line("Hook", hook, 20),
              style_line("Callout", dict(call, font=hook.get("font", cap["font"]),
                                         outline_width=16, shadow=0), 16),
              "", "[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    layout = []

    soft = {"Caption": cap.get("soft_shadow"), "Hook": hook.get("soft_shadow"), "Callout": False}

    def event(layer, start, end, name, top, text, w, h, label):
        when = f"{ass_time(start)},{ass_time(end)},{name},,0,0,{int(top)},,"
        if soft.get(name):
            # Soft shadow on its own layer underneath, so the letters themselves stay sharp
            # (blurring the text event itself is what made captions look fuzzy).
            lines.append(f"Dialogue: {layer * 2},{when}{{\\1a&HFF&\\3a&H90&\\bord7\\shad0\\blur9}}{text}")
        lines.append(f"Dialogue: {layer * 2 + 1},{when}{text}")
        layout.append({"start": start, "end": end, "x1": (W - w) / 2, "x2": (W + w) / 2,
                       "y1": top - 6, "y2": top + h + 6, "label": label})

    blocks = []   # windows where captions step aside for a hook/callout in their spot
    cap_moves = []  # (start, end, top): captions sit lower while a chest hook is up
    chin_line = max(chins) if chins else cap_top - cap.get("gap_below_chin", 40)

    # Hook: static title from frame 0, hard cut off. Above the head if it fits, else the captions' spot.
    htext = ((plan.get("hook") or {}).get("text") or "").strip()
    hstyle = (plan.get("hook") or {}).get("style") or hook.get("template")
    if hook.get("enabled", True) and htext and hstyle and work is not None:
        # Locked template drawn as a crisp image (see hooks.py). The AI only picks the
        # style, the words and top vs chest; size, breaks and spacing are measured here.
        secs = min(float(plan["hook"].get("seconds", hook.get("seconds_on_screen", 3))), tl.total)
        t = hooks.template(hstyle, style)
        where = (plan.get("hook") or {}).get("position") or t.get("position") or hook.get("position", "top")
        res, top = None, None
        if where != "chest":
            top_y = max(hook.get("position_from_top", 230), SAFE_TOP + 20)
            heads = [f["head_top"] for f in clip_faces if f and f["o_s"] < secs and f["o_e"] > 0]
            room = (min(heads) if heads else H * 0.3) - 24 - top_y   # clear of hair / cap
            if room >= t.get("min_top_room", 200):
                res = hooks.render(htext, t, W - 2 * TOP_SIDE, room, ws)
            if res:
                top = top_y
            else:
                notes.append("Hook moved onto the chest because there isn't room above your head.")
        if not res:   # across the chest, clear of the right-side buttons
            top = chin_line + 60
            room = max_cap_top - top
            res = hooks.render(htext, t, W - SIDE - RIGHT_BUTTONS, room, ws) if room > 0 else None
            if not res:   # no clean gap under the chin: smallest size, sitting as low as allowed
                res = hooks.render(htext, dict(t, min_size=int(t["min_size"] * 0.7)),
                                   W - SIDE - RIGHT_BUTTONS, max(room, t["min_size"] * 2.2), ws)
                if not res:
                    fail("The hook is too long to fit. Use fewer words.")
                notes.append("Tight framing: the hook may touch your chin. Film with more room "
                             "below your face, or use a shorter hook.")
            top = min(top, max_cap_top - (res[1][3] - res[1][1]))
        img, ink, _ = res
        iw, ih = ink[2] - ink[0], ink[3] - ink[1]
        chest_w = W - SIDE - RIGHT_BUTTONS
        x = SIDE + (chest_w - iw) / 2 if top > H * 0.45 and iw <= chest_w else (W - iw) / 2
        png = work / "hook.png"
        img.save(png)
        if top > H * 0.45:   # chest: captions tuck under it, or wait if there's no room
            below = top + ih + 22
            if below <= max_cap_top:
                cap_moves.append((0, secs, max(below, cap_top)))
            else:
                blocks.append([0, secs])
        layout.append({"start": 0, "end": secs, "x1": x, "x2": x + iw, "y1": top, "y2": top + ih,
                       "label": "hook", "png": str(png), "px": int(x - ink[0]), "py": int(top - ink[1])})
    elif hook.get("enabled", True) and htext:
        secs = min(float(plan["hook"].get("seconds", hook.get("seconds_on_screen", 3))), tl.total)
        plain = htext.replace("*", "")
        hpad = hook.get("outline_width", 20) if hook.get("box_color") else 0
        hw, hh, _ = text_box(plain, hook["size"], hook.get("uppercase"), W, hpad)
        top = hook.get("position_from_top", 250)
        chest = hook.get("position", "top") == "chest"
        if not chest and not fits_top(hh, top, 0, secs):
            chest = True
            notes.append("Hook moved onto the chest because your head is near the top of the frame.")
        if chest:
            top = min(chin_line + 36, max_cap_top - hh)
            below = top + hh + 22
            if below <= max_cap_top:
                cap_moves.append((0, secs, max(below, cap_top)))   # captions tuck under the hook
            else:
                blocks.append([0, secs])                            # no room: captions wait
        txt = ass_text(htext)
        if hook.get("uppercase"):
            txt = txt.upper()
        hi, base = ass_color(hook.get("highlight_color", hook["text_color"])), ass_color(hook["text_color"])
        txt = re.sub(r"\*([^*]+)\*", lambda m: f"{{\\c{hi}}}{m.group(1)}{{\\c{base}}}", txt)
        event(2, 0, secs, "Hook", top, txt, hw, hh, "hook")

    # Callouts: above the head if they fit, otherwise they replace the captions for a moment.
    for c in plan.get("callouts", []):
        s = tl.map(float(c["start"]))
        e = tl.map(float(c.get("end", float(c["start"]) + 2)))
        if e - s < 0.5:
            e = min(s + 1.5, tl.total)
        text = c["text"].upper() if call.get("uppercase") else c["text"]
        cw, chh, _ = text_box(text, call["size"], call.get("uppercase"), W, 16)
        top = hook.get("position_from_top", 250)
        if not fits_top(chh, top, s, e) or any(b["label"] == "hook" and b["end"] > s for b in layout):
            top = cap_top
            blocks.append([s, e])
        event(2, s, e, "Callout", top,
              "{\\fscx70\\fscy70\\t(0,110,\\fscx104\\fscy104)\\t(110,170,\\fscx100\\fscy100)}" + ass_text(text),
              cw, chh, "callout")

    # Captions.
    if cap.get("enabled", True):
        hide = set(plan.get("caption_hide", []))
        words = []
        for w in plan["words"]:
            if w["n"] in hide or w["word"] == "(sound)" or not tl.contains((w["start"] + w["end"]) / 2):
                continue
            text = w["word"]
            # a cut 'Um,' at the start of a sentence leaves 'the reason...': capitalize it
            if (not words or re.search(r"[.?!]$", words[-1]["word"])) and text[:1].islower():
                text = text[:1].upper() + text[1:]
            words.append({"word": text, "start": tl.map(w["start"]), "end": tl.map(w["end"])})
        max_chars = int((W - 2 * SIDE - pad * 2) / char_w(cap["size"], cap_upper))
        chunks = caption_chunks(words, int(cap.get("words_per_caption", 3)), max_chars)
        hi, base = ass_color(cap["highlight_color"]), ass_color(cap["text_color"])
        line_mode = cap.get("mode", "word") == "line"
        for ci, chunk in enumerate(chunks):
            nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else tl.total
            chunk_end = min(chunk[-1]["end"] + 0.35, nxt)
            steps = [(chunk[0]["start"], chunk_end, None)] if line_mode else [
                (w["start"], chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end, wi)
                for wi, w in enumerate(chunk)]
            plain = " ".join(x["word"].strip(",;:") for x in chunk)
            if cap_upper:
                plain = plain.upper()
            cw_, _, _ = text_box(plain, cap["size"], cap_upper, W, pad)
            splits = sorted({t for a, b, _ in cap_moves for t in (a, b)})
            for start, end, wi in steps:
                pieces = clip_windows(start, end, blocks)
                for t in splits:
                    pieces = [q for a, b in pieces for q in ([[a, t], [t, b]] if a < t < b else [[a, b]])]
                for s, e in pieces:
                    top_here = next((tp for a, b, tp in cap_moves if a <= s < b), cap_top)
                    parts = []
                    for k, x in enumerate(chunk):
                        t = ass_text(x["word"].strip(",;:"))
                        t = t.upper() if cap_upper else t
                        parts.append(f"{{\\c{hi}}}{t}{{\\c{base}}}" if k == wi else t)
                    anim = ""
                    if cap.get("pop_animation") and (wi in (0, None)) and s == start:
                        anim = "{\\fscx90\\fscy90\\t(0,80,\\fscx103\\fscy103)\\t(80,140,\\fscx100\\fscy100)}"
                    event(0, s, e, "Caption", top_here, anim + " ".join(parts), cw_, cap_h, "caption")
    return "\n".join(lines) + "\n", layout, notes, cap_top


# ---------- render ----------

def clip_plan(keep, tl, samples, sw, sh, W, H, style, extra_zooms, words=()):
    """Split clips at extra-zoom edges and give each clip its zoom and framing.
    The punch-in only switches at a sentence boundary, never mid-thought."""
    z = style.get("zoom", {})
    zamt = float(z.get("amount", 1.12))
    pieces = []
    zoomed = False
    for k, (s, e) in enumerate(keep):
        if k > 0:
            before = [w for w in words if w["end"] <= s + 0.05 and w["start"] >= keep[k - 1][0]]
            if not before or re.search(r"[.?!]$", before[-1]["word"]):
                zoomed = not zoomed
        base = zamt if (z.get("punch_in_on_cuts", True) and zoomed) else 1.0
        edges = sorted({s, e, *[t for zr in extra_zooms for t in (zr["start"], zr["end"]) if s < t < e]})
        for a, b in zip(edges, edges[1:]):
            mid = (a + b) / 2
            zoom = max([base] + [float(zr.get("amount", zamt + 0.08)) for zr in extra_zooms
                                 if zr["start"] <= mid < zr["end"]])
            pieces.append({"s": a, "e": b, "zoom": zoom})
    for p in pieces:
        box = facemod.face_in_range(samples, p["s"], p["e"]) if samples else None
        p["crop"], p["face"] = framing(box, p["zoom"], sw, sh, W, H)
        if p["face"]:
            cw, ch, x0, y0 = p["crop"]
            near = [x["box"] for x in samples
                    if x["box"] and p["s"] - 0.5 <= x["t"] <= p["e"] + 0.5
                    and 0.7 < x["box"][3] / box[3] < 1.4          # skip misdetections (hands etc.)
                    and abs(x["box"][1] - box[1]) < box[3] * 0.8] or [box]
            ys = [((b[1] + b[3]) * sh - y0) / ch * H for b in near]
            tops = [(b[1] * sh - y0) / ch * H for b in near]
            p["face"]["chin"] = max(p["face"]["chin"], max(ys))      # lowest the chin goes
            p["face"]["y1"] = min(p["face"]["y1"], min(tops))        # highest the forehead goes
    return pieces


def render_clip(i, p, src, work, W, H, fps, preview):
    dur = p["e"] - p["s"]
    fade_in, fade_out = p.get("fade_in", True), p.get("fade_out", True)
    cw, ch, x0, y0 = p["crop"]
    ow, oh = (W // 2, H // 2) if preview else (W, H)
    out = work / "clips" / f"clip_{i:03d}.mkv"
    fade = min(0.012, dur / 4)
    fades = []
    if fade_in:
        fades.append(f"afade=t=in:d={fade:.3f}")
    if fade_out:
        fades.append(f"afade=t=out:st={dur - fade:.4f}:d={fade:.3f}")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{p['s']:.4f}", "-i", str(src), "-t", f"{dur:.4f}",
         "-map", "0:v:0", "-map", "0:a:0",
         "-vf", f"crop={cw}:{ch}:{x0}:{y0},scale={ow}:{oh}:flags=lanczos,fps={fps},setsar=1,format=yuv420p",
         "-af", ",".join(["aresample=48000"] + fades),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "24" if preview else "14",
         "-c:a", "pcm_s16le", str(out)])
    return out


def render(plan_path, ws, preview=False):
    require_ffmpeg()
    plan_path = Path(plan_path)
    plan = load_json(plan_path)
    style, rules = load_config(ws)
    work = plan_path.parent
    src = Path(plan["source"])
    if not src.exists():
        fail(f"Original video not found: {src}")
    info = probe(src)
    if not info["has_audio"]:
        fail("This video has no audio track.")

    o, aud = rules["output"], rules["audio"]
    W, H, fps = int(o["width"]), int(o["height"]), int(o["fps"])
    audio = load_audio(work, src)
    keep = final_keep(plan, fps, audio)
    tl = Timeline(keep)
    levels = edge_levels(keep, *audio)
    # Only real jumps count: where two clips meet seamlessly in the original, the audio is continuous.
    jump_before = [i > 0 and keep[i][0] - keep[i - 1][1] > 0.02 for i in range(len(keep))]
    jump_after = [i < len(keep) - 1 and keep[i + 1][0] - keep[i][1] > 0.02 for i in range(len(keep))]
    # ...and sound we meant to throw away (a cut um, retake or scrap right next to the cut) is fine.
    def mid(w):
        return (w["start"] + w["end"]) / 2
    kept = [w for w in plan["words"] if any(a <= mid(w) < b for a, b in keep)]
    gone = [w for w in plan["words"] if w not in kept]

    def removed_next_to(t, side):
        return any((0 <= w["start"] - t < 0.15) if side == "after" else (0 <= t - w["end"] < 0.15)
                   for w in gone)
    dirty = [(i, a, b) for i, (a, b) in enumerate(levels)
             if (jump_before[i] and a > 24 and not removed_next_to(keep[i][0], "before"))
             or (jump_after[i] and b > 24 and not removed_next_to(keep[i][1], "after"))]
    if tl.total > o.get("max_length_seconds", 1e9):
        print(f"Heads up: finished video is {fmt_time(tl.total)}, longer than your "
              f"{o['max_length_seconds']}s max.")

    samples = facemod.track(src, work)
    clips = clip_plan(keep, tl, samples, info["width"], info["height"], W, H, style,
                      plan.get("extra_zooms", []), plan["words"])

    # Fonts: the user's own fonts in Rules/fonts win, else the bundled ones.
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    for sect in ("captions", "hook"):
        ff = style[sect].get("font_file")
        for d in (ws / "Rules" / "fonts", ASSETS / "fonts"):
            if ff and (d / ff).exists():
                shutil.copy(d / ff, fonts / ff)
                break

    t = 0.0
    for c in clips:
        if c["face"]:
            c["face"]["o_s"], c["face"]["o_e"] = t, t + c["e"] - c["s"]
        t += c["e"] - c["s"]
    ass, layout, notes, cap_top = build_layout(plan, tl, style, W, H, [c["face"] for c in clips],
                                               work=work, ws=ws)
    (work / "captions.ass").write_text(ass, encoding="utf-8")

    for k, c in enumerate(clips):   # no fade where two clips continue seamlessly
        c["fade_in"] = k == 0 or c["s"] - clips[k - 1]["e"] > 0.02
        c["fade_out"] = k == len(clips) - 1 or clips[k + 1]["s"] - c["e"] > 0.02

    # 1) Every clip straight from the original, framed on the face.
    (work / "clips").mkdir(exist_ok=True)
    for old in (work / "clips").glob("*.mkv"):
        old.unlink()
    print(f"Cutting {len(clips)} clips...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        files = list(pool.map(lambda a: render_clip(a[0], a[1], src, work, W, H, fps, preview),
                              enumerate(clips)))
    (work / "clips" / "concat.txt").write_text("".join(f"file '{f.name}'\n" for f in files))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt",
         "-c", "copy", str(work / "aroll.mkv")], cwd=work / "clips")

    # 2) Text, sound effects, music, loudness.
    inputs = ["-i", "aroll.mkv"]
    sfx = []
    if aud.get("sound_effects", True):
        if aud.get("whoosh_on_hook", True) and any(b["label"] == "hook" for b in layout):
            sfx.append(("whoosh.wav", 0.0))
        if aud.get("pop_on_callouts", True):
            sfx += [("pop.wav", b["start"]) for b in layout if b["label"] == "callout"]
    for name, _ in sfx:
        inputs += ["-i", str(ASSETS / "sfx" / name)]
    music_idx = None
    if plan.get("music"):
        m = Path(plan["music"])
        if not m.is_absolute():
            m = ws / "Music" / m
        if not m.exists():
            fail(f"Music file not found: {m}")
        music_idx = 1 + len(sfx)
        inputs += ["-stream_loop", "-1", "-i", str(m)]

    af = [f"[0:a]loudnorm=I={aud.get('loudness_lufs', -14)}:TP=-1.5:LRA=11,aresample=48000[voice]"]
    mix = ["[voice]"]
    if music_idx is not None:
        af.append("[voice]asplit=2[v1][v2]")
        af.append(f"[{music_idx}:a]aresample=48000,volume={aud.get('music_volume', 0.1)}[mus]")
        af.append("[mus][v2]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[duck]")
        mix = ["[v1]", "[duck]"]
    for k, (_, at) in enumerate(sfx):
        ms = int(at * 1000)
        af.append(f"[{k + 1}:a]aresample=48000,volume={aud.get('sfx_volume', 0.35)},adelay={ms}|{ms}[s{k}]")
        mix.append(f"[s{k}]")
    if len(mix) > 1:
        af.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,alimiter=limit=0.95[a]")
    else:
        af.append("[voice]anull[a]")
    # Hook images go on top of the captions layer, at full sharpness.
    vf, last = ["[0:v]subtitles=captions.ass:fontsdir=fonts[t0]"], "t0"
    k = 0.5 if preview else 1.0
    for n, b in enumerate(x for x in layout if x.get("png")):
        idx = inputs.count("-i")
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{b['end']:.3f}", "-i", b["png"]]
        scale = f"[{idx}:v]scale=iw*{k}:ih*{k}[h{n}];" if preview else f"[{idx}:v]null[h{n}];"
        vf.append(f"{scale}[{last}][h{n}]overlay={int(b['px'] * k)}:{int(b['py'] * k)}:eof_action=pass[t{n + 1}]")
        last = f"t{n + 1}"
    vf.append(f"[{last}]format=yuv420p[v]")
    graph = ";".join(vf) + ";" + ";".join(af)
    (work / "filter.txt").write_text(graph, encoding="utf-8")

    out = Path(plan["output"])
    if preview:
        out = out.with_name(out.stem + " (preview).mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Rendering {fmt_time(tl.total)} video...")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph,
         "-map", "[v]", "-map", "[a]", "-t", f"{tl.total:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "28" if preview else "19",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], cwd=work)

    # 3) Check our own work.
    from verify import verify  # noqa: E402
    report = verify(out, layout, tl, W, H, work)

    print(f"\nDONE: {out}")
    print(f"Length: {fmt_time(info['duration'])} -> {fmt_time(tl.total)}   Clips: {len(clips)}")
    for n in notes:
        print(f"Note: {n}")
    if dirty:
        print(f"CUT CHECK: {len(dirty)} cut(s) land on sound (a word may be clipped or bleed in):")
        for i, a, b in dirty:
            print(f"  - clip {i + 1} at {fmt_time(keep[i][0])}: start +{a:.0f} dB, end +{b:.0f} dB above room tone")
    else:
        print(f"CUT CHECK: OK. All {len(keep) - 1} cuts land in silence.")
    print(report)
    print(f"Frames: {work / 'check.jpg'}   Cuts: {work / 'cuts.jpg'}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--preview", action="store_true", help="Fast half-size draft")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    if not Path(a.plan).exists():
        fail(f"Can't find {a.plan}")
    render(a.plan, workspace(a.workspace), a.preview)


if __name__ == "__main__":
    main()
