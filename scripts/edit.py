"""One command to edit a video.

  python scripts/edit.py                 # prepare the newest video in Raw/
  python scripts/edit.py "my video.mp4"  # prepare a specific video
  python scripts/edit.py --auto          # prepare AND render, no review (setup test)

"Prepare" = transcribe + build plan.json. Claude then reviews the plan
(retakes, hook, callouts) and runs render.py.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import fail, newest_raw, work_dir, workspace  # noqa: E402
from plan import make_plan  # noqa: E402
from render import render  # noqa: E402
from transcribe import transcribe  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?")
    ap.add_argument("--auto", action="store_true", help="Render straight away")
    ap.add_argument("--fresh", action="store_true", help="Re-transcribe and rebuild the plan")
    ap.add_argument("--workspace")
    a = ap.parse_args()

    ws = workspace(a.workspace)
    if a.video:
        v = Path(a.video)
        if not v.exists():
            v = ws / "Raw" / a.video
        if not v.exists():
            fail(f"Can't find the video: {a.video}")
    else:
        v = newest_raw(ws)
    print(f"Video: {v.name}")

    wd = work_dir(ws, v)
    t = wd / "transcript.json"
    if a.fresh or not t.exists():
        transcribe(v, ws)
    else:
        print("Using the existing transcript (add --fresh to redo it).")
    p = wd / "plan.json"
    if a.fresh or not p.exists():
        make_plan(t, ws)
    else:
        print(f"Using the existing plan: {p}")

    if a.auto:
        render(p, ws)
    else:
        print(f"\nNext: review {wd / 'transcript.txt'}, edit {p}, then run:")
        print(f'  python scripts/render.py "{p}"')


if __name__ == "__main__":
    main()
