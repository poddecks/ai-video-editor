"""Transcribe a video with word-level timestamps (runs locally, nothing uploaded).

Usage: python scripts/transcribe.py "Raw/my video.mp4"
Writes .work/<video name>/transcript.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (fail, load_config, probe, require_ffmpeg, run, save_json,  # noqa: E402
                    work_dir, workspace)

# Nudges Whisper to keep "um", "uh" and repeats instead of tidying them away,
# so the editor can find and cut them.
VERBATIM_PROMPT = "Umm, so, uh, I was like, you know, I mean... Hmm. Okay, so, um, here's the thing."


def transcribe(video, ws, model_name=None):
    require_ffmpeg()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        fail("Transcription isn't installed. Run: python -m pip install faster-whisper")

    _, rules = load_config(ws)
    tcfg = rules.get("transcription", {})
    model_name = model_name or tcfg.get("model", "small.en")
    out = work_dir(ws, video)
    info = probe(video)
    if not info["has_audio"]:
        fail("This video has no audio track, so there's nothing to transcribe.")

    audio = out / "audio.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:a:0", "-ac", "1",
         "-ar", "16000", str(audio)])

    print(f"Transcribing with the '{model_name}' model "
          "(the first run downloads it, which can take a few minutes)...")
    model = WhisperModel(model_name, device="auto", compute_type="int8")
    # Hand Whisper the samples directly: its own file reader breaks with some PyAV versions.
    import wave
    import numpy as np
    with wave.open(str(audio)) as wf:
        samples = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768
    segments, _ = model.transcribe(
        samples, language=tcfg.get("language") or None, word_timestamps=True,
        initial_prompt=VERBATIM_PROMPT, vad_filter=False, condition_on_previous_text=False)

    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"word": text, "start": round(w.start, 3),
                              "end": round(w.end, 3)})
    if not words:
        fail("No speech was found in this video.")

    data = {"source": str(Path(video).resolve()), "duration": info["duration"],
            "words": words}
    save_json(out / "transcript.json", data)
    print(f"Transcript saved: {out / 'transcript.json'} ({len(words)} words)")
    return out / "transcript.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", help="tiny.en, base.en, small.en, medium.en, large-v3")
    ap.add_argument("--workspace")
    a = ap.parse_args()
    ws = workspace(a.workspace)
    v = Path(a.video)
    if not v.exists():
        v = ws / "Raw" / a.video
    if not v.exists():
        fail(f"Can't find the video: {a.video}")
    transcribe(v, ws, a.model)


if __name__ == "__main__":
    main()
