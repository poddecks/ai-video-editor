"""Check a finished video: no text over a face, and a picture of every cut.

Called automatically by render.py. Writes verify.txt, check.jpg and cuts.jpg.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import faces as facemod  # noqa: E402
from common import fmt_time, run  # noqa: E402


def _overlap(a, b):
    return a["x1"] < b["x2"] and b["x1"] < a["x2"] and a["y1"] < b["y2"] and b["y1"] < a["y2"]


def verify(video, layout, tl, W, H, work, max_samples=40):
    work = Path(work)
    lines = []
    cv2, np = facemod._cv2()

    # Contact sheet of 8 evenly spaced frames.
    step = max(tl.total / 8, 0.5)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
         f"fps=1/{step:.3f},scale=270:-2,tile=4x2:padding=6:color=white",
         "-frames:v", "1", str(work / "check.jpg")])

    if cv2 is None:
        msg = "Face check skipped (face detection not installed)."
        (work / "verify.txt").write_text(msg)
        return msg

    # 1) Text vs face, measured on the FINAL video.
    times = sorted({round((b["start"] + b["end"]) / 2, 2) for b in layout})
    if len(times) > max_samples:
        times = [times[int(i * len(times) / max_samples)] for i in range(max_samples)]
    frames, _, fw, fh = facemod.read_frames(video, 0, width=360, times=times)
    boxes = facemod.detect_frames([f for f in frames if f is not None], fw, fh)
    problems, checked = [], 0
    bi = 0
    for t, f in zip(times, frames):
        if f is None:
            continue
        box = boxes[bi]
        bi += 1
        if not box:
            continue
        checked += 1
        face = {"x1": box[0] * W, "x2": (box[0] + box[2]) * W,
                "y1": (box[1] + box[3] * 0.22) * H, "y2": (box[1] + box[3]) * H}
        for b in layout:
            if b["start"] <= t < b["end"] and _overlap(face, b):
                problems.append(f"{fmt_time(t)}  {b['label']} touches the face")
    if problems:
        lines.append(f"FACE CHECK: {len(problems)} problem(s) in {checked} frames checked:")
        lines += [f"  - {p}" for p in dict.fromkeys(problems)]
        lines.append("  Fix: raise captions.gap_below_chin, lower the hook, or film with more room under the chin.")
    else:
        lines.append(f"FACE CHECK: OK. No text over a face in {checked} frames checked.")

    # 2) Both sides of every cut, so you can eyeball them in one image.
    cuts = tl.offsets[1:][:16]
    if cuts:
        tts = [t for c in cuts for t in (max(c - 0.05, 0), c + 0.02)]
        fr, _, w, h = facemod.read_frames(video, 0, width=150, times=tts)
        blank = np.full((h, w, 3), 255, np.uint8)
        pairs = []
        for k in range(0, len(fr), 2):
            a = fr[k] if fr[k] is not None else blank
            b = fr[k + 1] if k + 1 < len(fr) and fr[k + 1] is not None else blank
            gap = np.full((h, 4, 3), 40, np.uint8)
            pairs.append(np.hstack([a, gap, b]))
        cols = 4
        rows = []
        pad = np.full((h, 12, 3), 255, np.uint8)
        for r in range(0, len(pairs), cols):
            row = pairs[r:r + cols]
            while len(row) < cols:
                row.append(np.full_like(pairs[0], 255))
            parts = []
            for p in row:
                parts += [p, pad]
            rows.append(np.hstack(parts))
            rows.append(np.full((12, rows[-1].shape[1], 3), 255, np.uint8))
        cv2.imwrite(str(work / "cuts.jpg"), np.vstack(rows))
        lines.append(f"CUTS: {len(tl.offsets) - 1} cuts. Before/after frames of each are in cuts.jpg.")
    report = "\n".join(lines)
    (work / "verify.txt").write_text(report, encoding="utf-8")
    return report
