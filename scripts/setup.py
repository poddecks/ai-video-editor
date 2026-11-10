"""Create the AI Editor folder and check that everything is installed.

Usage:
  python scripts/setup.py                      # folder on your Desktop
  python scripts/setup.py --folder "D:/AI Editor"
  python scripts/setup.py --check              # only run the checks
"""
import argparse
import importlib.util
import json
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FOLDERS, POINTER, TEMPLATES, workspace  # noqa: E402

OK, BAD = "[OK]  ", "[MISSING]"


def default_folder():
    desk = Path.home() / "Desktop"
    return (desk if desk.exists() else Path.home()) / "AI Editor"


def has_subtitles(ffmpeg):
    """The regular Homebrew ffmpeg can't burn in captions; ffmpeg-full can."""
    import subprocess
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return True  # don't block setup if the check itself fails
    return any(line.split()[1:2] == ["subtitles"] for line in out.splitlines())


def check():
    system = platform.system()
    problems = []
    print("\nChecking your setup...\n")

    py_ok = sys.version_info >= (3, 9)
    print(f"{OK if py_ok else BAD} Python {platform.python_version()}")
    if not py_ok:
        problems.append("Python 3.9 or newer is needed.")

    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        print(f"{OK if found else BAD} {tool}")
        if not found:
            hint = {"Darwin": "brew install ffmpeg",
                    "Windows": "winget install Gyan.FFmpeg  (then reopen the terminal)"}
            problems.append(f"Install FFmpeg: {hint.get(system, 'sudo apt install ffmpeg')}")
        elif tool == "ffmpeg" and not has_subtitles(found):
            print(f"{BAD} ffmpeg can't draw captions (no subtitles filter)")
            fix = {"Darwin": "brew install ffmpeg-full, then make it the default ffmpeg"}
            problems.append(f"Install an FFmpeg build with captions support: "
                            f"{fix.get(system, 'install an FFmpeg build with libass')}")

    for mod, label, pkg in (("faster_whisper", "faster-whisper (transcription)", "faster-whisper"),
                            ("cv2", "OpenCV (face detection)", "opencv-python-headless"),
                            ("PIL", "Pillow (hook titles)", "Pillow")):
        found = importlib.util.find_spec(mod) is not None
        print(f"{OK if found else BAD} {label}")
        if not found:
            problems.append(f"Install {label.split(' (')[0]}: python -m pip install {pkg}")

    free_gb = shutil.disk_usage(Path.home()).free / 1e9
    disk_ok = free_gb >= 20
    print(f"{OK if disk_ok else BAD} Free disk space: {free_gb:.0f} GB (50 GB+ recommended)")
    if not disk_ok:
        problems.append("Free up disk space. Video files are big.")

    print()
    if problems:
        print("Fix these, then run this check again:")
        for p in dict.fromkeys(problems):
            print(f"  - {p}")
        return False
    print("Everything is installed. You're ready to edit.")
    return True


def make_folder(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in FOLDERS:
        (folder / name).mkdir(exist_ok=True)
    rules = folder / "Rules"
    for f in TEMPLATES.iterdir():
        dest = rules / f.name
        if not dest.exists():  # never overwrite their customizations
            shutil.copy(f, dest)
    POINTER.write_text(json.dumps({"workspace": str(folder)}, indent=2))
    print(f"\nYour AI Editor folder is ready: {folder}")
    for name in FOLDERS:
        print(f"  {name}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", help="Where to create the AI Editor folder")
    ap.add_argument("--check", action="store_true", help="Only check installs")
    args = ap.parse_args()
    if not args.check:
        folder = Path(args.folder).expanduser().resolve() if args.folder else (
            workspace() if POINTER.exists() else default_folder())
        make_folder(folder)
    sys.exit(0 if check() else 1)


if __name__ == "__main__":
    main()
