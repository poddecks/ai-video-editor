"""Find the speaker's face through the video (runs locally, YuNet model bundled).

Used by render.py to center punch-ins on the face, put captions under the chin,
and check that no text ever covers a face.
"""
import json
import statistics
import subprocess
from pathlib import Path

from common import ASSETS, probe

MODEL = ASSETS / "models" / "yunet.onnx"
SAMPLE_W = 480


def _cv2():
    try:
        import cv2
        import numpy as np
        try:
            cv2.setLogLevel(0)
        except Exception:
            pass
        return cv2, np
    except ImportError:
        return None, None


def detect_frames(frames_bgr, w, h):
    """Return the biggest face per frame as normalized (x, y, w, h), or None."""
    cv2, _ = _cv2()
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.7, 0.3, 50)
    out = []
    for f in frames_bgr:
        _, faces = det.detect(f)
        if faces is None or len(faces) == 0:
            out.append(None)
            continue
        x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])[:4]
        out.append([float(x) / w, float(y) / h, float(fw) / w, float(fh) / h])
    return out


def read_frames(video, fps, width=SAMPLE_W, times=None):
    """Decode frames as BGR arrays: every 1/fps seconds, or at specific times."""
    cv2, np = _cv2()
    info = probe(video)
    w = width
    h = int(round(info["height"] * width / info["width"] / 2) * 2)
    if times is None:
        cmd = ["ffmpeg", "-v", "error", "-i", str(video), "-vf",
               f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        n = len(raw) // (w * h * 3)
        frames = [np.frombuffer(raw, np.uint8, w * h * 3, i * w * h * 3).reshape(h, w, 3)
                  for i in range(n)]
        return frames, [i / fps for i in range(n)], w, h
    frames = []
    for t in times:
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
               "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        raw = subprocess.run(cmd, capture_output=True).stdout
        frames.append(np.frombuffer(raw[:w * h * 3], np.uint8).reshape(h, w, 3)
                      if len(raw) >= w * h * 3 else None)
    return frames, list(times), w, h


def track(video, work, fps=2):
    """Face per sample time for the source video, cached in faces.json."""
    cache = Path(work) / "faces.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        if data.get("source") == str(video):
            return data["samples"]
    cv2, _ = _cv2()
    if cv2 is None or not MODEL.exists():
        print("Face detection not available (pip install opencv-python-headless). "
              "Using center framing.")
        return []
    frames, times, w, h = read_frames(video, fps)
    boxes = detect_frames(frames, w, h)
    samples = [{"t": round(t, 2), "box": b} for t, b in zip(times, boxes)]
    cache.write_text(json.dumps({"source": str(video), "samples": samples}))
    found = sum(1 for s in samples if s["box"])
    print(f"Face found in {found}/{len(samples)} sample frames.")
    return samples


def face_in_range(samples, s, e):
    """Median face box between times s and e (falls back to the whole video)."""
    def med(boxes):
        return [statistics.median(b[i] for b in boxes) for i in range(4)]
    inside = [x["box"] for x in samples if x["box"] and s - 0.25 <= x["t"] <= e + 0.25]
    if inside:
        return med(inside)
    every = [x["box"] for x in samples if x["box"]]
    return med(every) if every else None
