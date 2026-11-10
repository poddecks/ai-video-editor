"""Shared helpers for the AI Video Editor skill."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
ASSETS = SKILL_DIR / "assets"
POINTER = Path.home() / ".ai-video-editor.json"
FOLDERS = ["Raw", "Finished", "References", "Rules", "Music"]
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def fail(msg):
    print(f"\nERROR: {msg}\n", file=sys.stderr)
    sys.exit(1)


def workspace(path=None):
    """Find the AI Editor folder: argument > env var > saved pointer."""
    if path:
        return Path(path).expanduser().resolve()
    env = os.environ.get("AI_EDITOR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if POINTER.exists():
        try:
            return Path(json.loads(POINTER.read_text())["workspace"])
        except Exception:
            pass
    fail("No AI Editor folder set up yet. Run: python scripts/setup.py")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif not str(k).startswith("_"):
            out[k] = v
    return out


def looks():
    return sorted(p.stem for p in (ASSETS / "looks").glob("*.json"))


def load_config(ws):
    """Style = chosen look + the user's overrides. Rules = rules.json."""
    def pick(name):
        p = ws / "Rules" / name
        return load_json(p if p.exists() else TEMPLATES / name)
    user = pick("style.json")
    look = user.get("look", "clean")
    preset = ASSETS / "looks" / f"{look}.json"
    if not preset.exists():
        fail(f"Unknown look '{look}'. Choose one of: {', '.join(looks())}")
    style = deep_merge(load_json(preset), user.get("overrides", {}))
    style["brand"] = user.get("brand", {})
    style["look"] = look
    return style, pick("rules.json")


def work_dir(ws, video):
    d = ws / ".work" / Path(video).stem
    d.mkdir(parents=True, exist_ok=True)
    return d


def require_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            fail(f"{tool} is not installed. Ask Claude: 'Install FFmpeg on this computer.'")


def run(cmd, cwd=None):
    """Run a command, show a short error if it fails."""
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-25:])
        fail(f"Command failed: {' '.join(str(c) for c in cmd[:4])} ...\n{tail}")
    return res


def probe(video):
    res = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(video)])
    info = json.loads(res.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)  # iPhones add a 2nd spatial track; we always use the first
    if not v:
        fail(f"No video stream found in {video}")
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) in (90, 270):
        w, h = h, w
    return {"duration": float(info["format"]["duration"]), "width": w,
            "height": h, "has_audio": a is not None}


def newest_raw(ws):
    vids = [p for p in (ws / "Raw").iterdir() if p.suffix.lower() in VIDEO_EXTS]
    if not vids:
        fail(f"No videos found in {ws / 'Raw'}. Drop a video in there first.")
    return max(vids, key=lambda p: p.stat().st_mtime)


def fmt_time(t):
    m, s = divmod(max(t, 0), 60)
    return f"{int(m)}:{s:05.2f}"


# ---------- audio-aware cut points ----------

def load_audio(work, video):
    """16 kHz mono samples of the video's main audio track (cached as audio.wav)."""
    import wave
    import numpy as np
    wav = Path(work) / "audio.wav"
    if not wav.exists():
        run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:a:0", "-ac", "1",
             "-ar", "16000", str(wav)])
    with wave.open(str(wav)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype("float32") / 32768
    return a, 16000


def snap_to_silence(keep, words, audio, sr, max_out=0.3, max_in=0.3):
    """Put every cut where the sound really starts and stops, like an editor listening.
    - Clip end: from the last word's end, walk forward to where the voice actually stops
      (30 ms below the speech threshold). Breaths and room sound after that are trimmed.
    - Clip start: from the first word's start, walk back to where the voice actually begins
      (word timestamps often run a little late).
    - If two lines run together with no gap, both clips meet at the quietest point between them."""
    import numpy as np
    hop = int(sr * 0.01)
    n = len(audio) // hop
    if n == 0 or not keep:
        return keep
    frames = audio[:n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    floor = np.percentile(db, 10)
    speech = np.percentile(db, 75)
    thr = min(floor + 18, speech - 18)
    quiet = db < thr

    def valley(t0, t1):
        i0, i1 = max(int(t0 * 100), 0), min(max(int(t1 * 100), int(t0 * 100) + 1), n)
        return (i0 + int(np.argmin(db[i0:i1]))) * 0.01

    def voice_stops(t, limit):
        i, lim = int(t * 100), min(int(limit * 100), n)
        while i < lim - 2:
            if quiet[i] and quiet[i + 1] and quiet[i + 2]:
                return i * 0.01 + 0.03
            i += 1
        return None

    def voice_starts(t, limit):
        i, lim = min(int(t * 100), n - 1), max(int(limit * 100), 0)
        while i > lim:
            if quiet[i]:
                return (i + 1) * 0.01 - 0.02
            i -= 1
        return None

    out = [list(k) for k in keep]
    w_starts = sorted(w["start"] for w in words)
    w_ends = sorted(w["end"] for w in words)
    for i, (s, e) in enumerate(out):
        prev_end = out[i - 1][1] if i else 0.0
        nxt_start = keep[i + 1][0] if i + 1 < len(keep) else e + max_out
        # never stretch into the next word or filler, even one that is being cut
        nxt_start = min([nxt_start] + [t - 0.03 for t in w_starts if t > e + 0.02])
        prev_end = max([prev_end] + [t + 0.03 for t in w_ends if t < s - 0.02])
        joined_next = i + 1 < len(keep) and nxt_start - e < 0.02     # lines run straight on
        joined_prev = i > 0 and s - keep[i - 1][1] < 0.02
        # end
        if joined_next:
            out[i][1] = valley(e - 0.08, e + 0.08)      # meet the next clip at the quietest point
        else:
            stop = voice_stops(e - 0.06, nxt_start)
            out[i][1] = stop if stop is not None else valley(e - 0.05, nxt_start)
        # start (never before the previous clip's end)
        if joined_prev:
            out[i][0] = prev_end
        else:
            begin = voice_starts(s + 0.06, max(s - max_in, prev_end))
            if begin is None and s - prev_end < 0.45:
                out[i][0] = prev_end          # no silence to cut on: keep the take continuous
            else:
                out[i][0] = max(begin if begin is not None else valley(max(s - max_in, prev_end), s + 0.06),
                                prev_end)
        if out[i][1] <= out[i][0] + 0.1:
            out[i][1] = out[i][0] + 0.1
    return [[round(a, 3), round(b, 3)] for a, b in out]


def edge_levels(keep, audio, sr):
    """Loudness (dB above the room's quiet level) just outside each clip's start and end."""
    import numpy as np
    def level(t0, t1):
        seg = audio[max(int(t0 * sr), 0):max(int(t1 * sr), 1)]
        return 20 * np.log10(np.sqrt((seg ** 2).mean()) + 1e-9) if len(seg) else -99
    hop = int(sr * 0.02)
    n = len(audio) // hop
    floor = np.percentile([level(i * 0.02, i * 0.02 + 0.02) for i in range(0, n, 5)], 10)
    # measure what the cut throws away, right outside each edge: loud there = a word got chopped
    return [(level(s - 0.03, s) - floor, level(e, e + 0.03) - floor) for s, e in keep]


def untranscribed_sounds(words, audio, sr, min_len=0.15):
    """Spoken sound the transcript doesn't account for (a mumbled 'um', a laugh, a false start
    the speech engine skipped). Returns [start, end] ranges inside gaps between words."""
    import numpy as np
    hop = int(sr * 0.01)
    n = len(audio) // hop
    if n == 0:
        return []
    db = 20 * np.log10(np.sqrt((audio[:n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-9)
    floor, speech = np.percentile(db, 10), np.percentile(db, 75)
    loud = db > max(floor + 22, speech - 14)
    found = []
    ws = sorted(words, key=lambda w: w["start"])
    for a, b in zip(ws, ws[1:]):
        g0, g1 = a["end"] + 0.15, b["start"] - 0.08     # leave room for word tails / onsets
        if g1 - g0 < min_len:
            continue
        i, i1 = int(g0 * 100), min(int(g1 * 100), n)
        while i < i1:
            if loud[i]:
                j = i
                while j < i1 and loud[j]:
                    j += 1
                if (j - i) / 100 >= min_len:
                    found.append([round(i / 100 - 0.03, 3), round(j / 100 + 0.05, 3)])
                i = j
            i += 1
    return found
