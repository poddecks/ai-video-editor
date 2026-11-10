"""Hook titles drawn as crisp images from locked templates.

The AI never designs a hook. It picks a template (bold, bubble, mixed), writes the
words and decides top or chest. Everything else is fixed here: font file, size range,
line breaks, line height, letter spacing, colors, box padding, shadow.

Templates live in SKILL/assets/hooks/<name>.json. A user's Rules/style.json can tweak
any template under "hook_styles", e.g. {"hook_styles": {"bold": {"color": "#FFFFFF"}}}.

Preview a hook without rendering a video:
  python scripts/hooks.py "How to tell a better story" --style bold
  python scripts/hooks.py "why i *QUIT $50k* a month" --style mixed --frame face.jpg
"""
import argparse
import itertools
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
STYLES = ("bold", "bubble", "mixed")
REF = 100  # measure text at this size, then scale (width is linear in size)
JOINERS = {"&", "and", "or", "but", "+", "-", "–", "—"}


# ---------- templates ----------

def deep_merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def template(name, style=None):
    """Load a hook template, with the user's tweaks from style['hook_styles'][name]."""
    name = (name or "bold").lower()
    p = ASSETS / "hooks" / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"Unknown hook style '{name}'. Choose one of: {', '.join(STYLES)}")
    t = json.loads(p.read_text(encoding="utf-8"))
    t = deep_merge(t, ((style or {}).get("hook_styles") or {}).get(name, {}))
    t["name"] = name
    return t


def font_path(file, ws=None):
    for d in ([Path(ws) / "Rules" / "fonts"] if ws else []) + [ASSETS / "fonts"]:
        if (d / file).exists():
            return str(d / file)
    raise SystemExit(f"Font file not found: {file} (put it in Rules/fonts or SKILL/assets/fonts)")


def rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(round(alpha * 255)))


# ---------- measuring and line breaks ----------

def line_width(font, text, size, tracking):
    return font.getlength(text) + tracking * size * max(len(text) - 1, 0)


def best_split(words, n, width_of):
    """Split words into n lines so the longest line is as short as possible (balanced)."""
    if n == 1 or len(words) < n:
        return [" ".join(words)]
    best, best_w = None, None
    for cuts in itertools.combinations(range(1, len(words)), n - 1):
        idx = (0,) + cuts + (len(words),)
        lines = [" ".join(words[idx[i]:idx[i + 1]]) for i in range(n)]
        w = max(width_of(s) for s in lines)
        # a line shouldn't start with a joining word ("& Playfulness" reads badly)
        if any(s.split()[0].lower() in JOINERS for s in lines[1:]):
            w *= 1.15
        if best_w is None or w < best_w - 0.5:
            best, best_w = lines, w
    return best


def fit(words, t, fpath, max_w, max_h, height_factor=1.0):
    """Pick line count and size. Returns (lines, size) or None if it can't fit at min_size."""
    ref = ImageFont.truetype(fpath, REF)
    tracking = t.get("tracking", 0)
    lh = t.get("line_height", 1.0)
    options = []
    for n in range(1, min(t.get("max_lines", 3), len(words)) + 1):
        lines = best_split(words, n, lambda s: line_width(ref, s, REF, tracking))
        widest = max(line_width(ref, s, REF, tracking) for s in lines)
        size = min(t["max_size"], max_w * REF / widest)
        if max_h:
            size = min(size, max_h / ((n - 1) * lh + 1.0) / height_factor)
        options.append((n, lines, size))
    good = [o for o in options if o[2] >= t["min_size"]]
    if not good:
        return None
    # fewest lines that reaches the target size; otherwise the biggest we can get
    target = [o for o in good if o[2] >= t.get("target_size", 0)]
    n, lines, size = target[0] if target else max(good, key=lambda o: o[2])
    return lines, int(size)


# ---------- drawing ----------

def draw_text(img, xy, text, font, size, fill, tracking, anchor="ls"):
    d = ImageDraw.Draw(img)
    if not tracking:
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
        return
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill, anchor=anchor)
        x += font.getlength(ch) + tracking * size


def add_shadow(layer, sh):
    if not sh:
        return layer
    a = layer.split()[3].filter(ImageFilter.GaussianBlur(sh.get("blur", 10)))
    a = a.point(lambda v: int(v * sh.get("alpha", 0.4)))
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(a)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (0, int(sh.get("offset", 4))))
    out.alpha_composite(layer)
    return out


def crop_to_ink(img, margin):
    box = img.split()[3].getbbox()
    if not box:
        return img, (0, 0, img.width, img.height)
    x1, y1, x2, y2 = box
    x1, y1 = max(x1 - margin, 0), max(y1 - margin, 0)
    x2, y2 = min(x2 + margin, img.width), min(y2 + margin, img.height)
    return img.crop((x1, y1, x2, y2)), (margin, margin, x2 - x1 - margin, y2 - y1 - margin)


def block(lines, t, fpath, size, color_of, align="center"):
    """Draw stacked lines; returns an RGBA image tightly around the ink (no shadow yet)."""
    font = ImageFont.truetype(fpath, size)
    tracking = t.get("tracking", 0)
    lh = t.get("line_height", 1.0)
    widths = [line_width(font, s, size, tracking) for s in lines]
    W = int(max(widths) + size)
    H = int(size * ((len(lines) - 1) * lh + 1.6))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base = size * 1.05
    for i, s in enumerate(lines):
        x = (W - widths[i]) / 2 if align == "center" else size / 2
        # word-by-word so *accent* words can take another color
        cx = x
        parts = color_of(s)
        for k, (word, accent) in enumerate(parts):
            piece = word + (" " if k < len(parts) - 1 else "")
            draw_text(img, (cx, base + i * size * lh), piece, font, size,
                      rgba(t["accent_color"] if accent and t.get("accent_color") else t["color"]), tracking)
            cx += line_width(font, piece, size, tracking) + (tracking * size if tracking else 0)
    return crop_to_ink(img, 0)[0]


def split_accent(text):
    """'how to *tell* a story' -> [('how', False), ('to', False), ('tell', True), ...]"""
    out, accent = [], False
    for tok in re.split(r"(\*)", text):
        if tok == "*":
            accent = not accent
            continue
        out += [(w, accent) for w in tok.split()]
    return out


def case(text, upper):
    return text.upper() if upper else text


# ---------- the three styles ----------

def render_stack(text, t, fpath, max_w, max_h):
    pad = t.get("box") or {}
    px, py = pad.get("pad_x", 0), pad.get("pad_y", 0)
    plain = case(text.replace("*", ""), t.get("uppercase"))
    accents = {i for i, (_, a) in enumerate(split_accent(text)) if a}
    words = plain.split()
    fitted = fit(words, t, fpath, max_w - 2 * px, (max_h - 2 * py) if max_h else None)
    if not fitted:
        return None
    lines, size = fitted
    # map accent flags back onto the fitted lines
    flags, i = [], 0
    for s in lines:
        n = len(s.split())
        flags.append([(w, (i + k) in accents) for k, w in enumerate(s.split())])
        i += n
    lookup = dict(zip(lines, flags))
    ink = block(lines, t, fpath, size, lambda s: lookup[s])
    if pad:
        W, H = ink.width + 2 * px, ink.height + 2 * py
        card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle((0, 0, W - 1, H - 1), radius=pad.get("radius", 28),
                                               fill=rgba(pad["color"]))
        card.alpha_composite(ink, (px, py))
        ink = card
    return ink, size


def render_mixed(text, t, fpath, max_w, max_h, ws=None):
    m = re.match(r"^\s*(.*?)\*(.+?)\*(.*?)\s*$", text)
    before, big, after = (m.group(1), m.group(2), m.group(3)) if m else ("", text, "")
    sm = t["small"]
    spath = font_path(sm["font_file"], ws)
    has_small = bool(before.strip() or after.strip())
    # small words add about 0.75 of a small line above and below the big text
    hf = 1 + (2 * 0.75 * sm["scale"] if has_small else 0)
    fitted = fit(case(big, t.get("uppercase")).split(), t, fpath, max_w, max_h, height_factor=hf)
    if not fitted:
        return None
    lines, size = fitted
    big_img = block(lines, t, fpath, size, lambda s: [(w, False) for w in s.split()])
    ssize = int(size * sm["scale"])
    sfont = ImageFont.truetype(spath, ssize)

    def small(txt):
        txt = case(txt.strip(), sm.get("uppercase"))
        w = int(sfont.getlength(txt) + ssize)
        im = Image.new("RGBA", (w, int(ssize * 1.8)), (0, 0, 0, 0))
        draw_text(im, (ssize / 2, ssize * 1.3), txt, sfont, ssize, rgba(sm["color"]), 0)
        im = crop_to_ink(im, 0)[0]
        return im.rotate(sm.get("rotate", 0), resample=Image.BICUBIC, expand=True)

    tops = [small(before)] if before.strip() else []
    bots = [small(after)] if after.strip() else []
    tuck = int(ssize * 0.35)                      # how far the small words overlap the big text
    top_h = tops[0].height - tuck if tops else 0
    bot_h = bots[0].height - tuck if bots else 0
    W = max([big_img.width] + [im.width for im in tops + bots])
    H = top_h + big_img.height + bot_h
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bx = (W - big_img.width) // 2
    canvas.alpha_composite(big_img, (bx, top_h))
    if tops:   # above, starting at the big text's left edge
        canvas.alpha_composite(tops[0], (max(bx - int(ssize * 0.1), 0), 0))
    if bots:   # below, ending at the big text's right edge
        canvas.alpha_composite(bots[0], (min(bx + big_img.width - bots[0].width, W - bots[0].width),
                                         top_h + big_img.height - tuck))
    if canvas.width > max_w:   # small words pushed it wide: scale the whole thing down
        k = max_w / canvas.width
        canvas = canvas.resize((int(canvas.width * k), int(canvas.height * k)), Image.LANCZOS)
        size = int(size * k)
    return canvas, size


def render(text, t, max_w, max_h=None, ws=None):
    """Draw a hook. Returns (RGBA image incl. shadow margin, ink box (x1,y1,x2,y2), size)
    or None if it can't fit at the template's minimum size."""
    fpath = font_path(t["font_file"], ws)
    res = render_mixed(text, t, fpath, max_w, max_h, ws) if t["name"] == "mixed" else \
        render_stack(text, t, fpath, max_w, max_h)
    if not res:
        return None
    ink, size = res
    sh = t.get("shadow")
    m = int((sh or {}).get("blur", 0) * 2 + (sh or {}).get("offset", 0)) + 2
    canvas = Image.new("RGBA", (ink.width + 2 * m, ink.height + 2 * m), (0, 0, 0, 0))
    canvas.alpha_composite(ink, (m, m))
    canvas = add_shadow(canvas, sh)
    return canvas, (m, m, m + ink.width, m + ink.height), size


# ---------- preview ----------

def main():
    ap = argparse.ArgumentParser(description="Preview a hook style on a frame")
    ap.add_argument("text")
    ap.add_argument("--style", default="bold", choices=STYLES)
    ap.add_argument("--frame", help="A 1080x1920 frame to draw on (optional)")
    ap.add_argument("--where", default="top", choices=("top", "chest"))
    ap.add_argument("--out", default="hook-preview.png")
    a = ap.parse_args()
    W, H, SIDE = 1080, 1920, 110
    t = template(a.style)
    res = render(a.text, t, W - 2 * SIDE, 620)
    if not res:
        sys.exit("That hook is too long to fit. Use fewer words.")
    img, ink, size = res
    bg = Image.open(a.frame).convert("RGBA").resize((W, H)) if a.frame else \
        Image.new("RGBA", (W, H), (60, 70, 60, 255))
    y = 220 if a.where == "top" else 1000
    bg.alpha_composite(img, ((W - img.width) // 2, y - ink[1]))
    bg.convert("RGB").save(a.out)
    print(f"Saved {a.out}  ({a.style}, size {size}px)")


if __name__ == "__main__":
    main()
