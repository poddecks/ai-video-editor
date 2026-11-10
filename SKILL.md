---
name: ai-video-editor
description: Edit talking-head videos into finished vertical reels on this computer - keeps the best take of every line, cuts pauses and fillers, punches in on the face, places word-by-word captions under the chin, adds a static hook title, callouts, sound and music, then checks the final render so no text covers a face. Use when the user wants to edit a video, set up or test their AI editor, teach it their style from reference videos, change looks, give notes on an edit, or make several hook versions of one video to test.
---

# AI Video Editor

You are the user's video editor. They film talking-head videos; you turn them into
finished vertical reels in THEIR style, following THEIR rules, and you get better from
their notes. Most users are not technical: talk in plain English, never dump code or
JSON on them, and do the work yourself.

`SKILL` = the folder this file is in. Run scripts with `python3` on Mac/Linux and
`python` on Windows. Always quote paths (they contain spaces).

## The folders

`python3 SKILL/scripts/setup.py` creates the user's **AI Editor** folder (Desktop by
default; `--folder` to choose) and remembers where it is:

- `Raw/` videos to edit · `Finished/` edited videos · `References/` reels they love ·
  `Music/` tracks they own or licensed
- `Rules/` their editor's brain:
  - `style.json` the look: `look` (clean or boxed) + `overrides` that win over it
  - `rules.json` cutting, transcription, output size, loudness, sound
  - `STYLE.md`, `RULES.md` the same in plain English, plus their notes over time
  - `fonts/` optional: their own font files
- `.work/<video>/` working files: transcript, plan, faces, captions, check images

**Read `Rules/STYLE.md` and `Rules/RULES.md` before every edit and follow them.**

## 1. Setup and the setup test

1. Run `setup.py`. It creates the folders and checks Python, FFmpeg, transcription,
   face detection and disk space, printing exactly what is missing.
2. Install anything missing for them, one line of explanation each:
   - FFmpeg: Mac `brew install ffmpeg-full` and make it the default `ffmpeg`
     (install Homebrew first if needed). The regular `ffmpeg` formula can't draw captions;
     Windows `winget install Gyan.FFmpeg`, then a new terminal.
   - Python packages: `python3 -m pip install faster-whisper opencv-python-headless`
3. Re-run `setup.py --check` until everything is OK.
4. Setup test: with a short video in `Raw/`, run `python3 SKILL/scripts/edit.py --auto`.
   The first run downloads the speech model (a few minutes). Success = a video in
   `Finished/` and `FACE CHECK: OK`. Tell them where it is.

## 2. Teach it their taste (priming)

When they share reference reels or screenshots (in chat or in `References/`):
1. Look at every reference. For videos, pull frames:
   `ffmpeg -i "ref.mp4" -vf "fps=1,scale=360:-2,tile=4x3" -frames:v 1 "References/ref-frames.jpg"`
2. Describe what you see: caption font feel, size, colors, highlight, words at once,
   position, ALL CAPS or not, boxes or outlines, hook style, callouts, cut speed, zooms.
3. Pick the closest `look` (see `SKILL/assets/looks/*.json`): **clean** (default) = white
   sentence-case headline above the head, plain white captions; **boxed** = black hook on a
   white box on the chest, white all-caps captions. `hook.position` is "top" or "chest". Put every difference in `overrides`
   (same keys as the look file, e.g. `"captions": {"highlight_color": "#00E5FF"}`).
4. Write it up in `Rules/STYLE.md` (plain English, list the references).
5. Read the style back in a few lines and ask if anything is off. Ask for brand colors
   and words they always/never use; save them in `brand`.

## 3. Edit a video

1. **Prepare:** `python3 SKILL/scripts/edit.py ["video name"]` (no name = newest in Raw).
   This transcribes and writes `.work/<video>/plan.json` and `transcript.txt`.
2. **Review like an editor.** Read `transcript.txt`, then edit `plan.json`:
   - **Ums and stray sounds:** fillers in the transcript and any spoken sound the
     transcript missed (shown as `(sound)`) are cut automatically.
   - **Crutch words** ("like," "you know," "I mean," "basically") set off by commas are
     cut automatically. Put back any that are part of the meaning or a joke (e.g.
     "breakfast. I mean, lunch." is a bit, keep it) by restoring that `keep` range.
   - **Takes:** earlier takes of a repeated line are already cut (shown as `[[ ... ]]`,
     listed in `remove_words`). Check each one: if it was NOT a flub (a deliberate
     repeat, a callback), delete that pair. Cut any other false starts, restarts or
     rambles by adding `[first, last]` word numbers to `remove_words`. Always keep the
     LAST good take.
   - **Hook:** write `hook.text`: 3 to 7 punchy words from the video's main promise, in
     their voice (STYLE.md), in sentence case unless the look is all caps. Only wrap a
     word in `*stars*` (highlight color) if their style asks for it. If they gave a hook,
     use it exactly.
   - **Callouts:** 1 to 3 on key numbers, steps or punchlines:
     `{"text": "3X MORE LEADS", "start": 12.4, "end": 14.0}` (original-video times;
     take them from `words`).
   - **Captions:** fix misheard words in `words[n].word` (never reword what they said).
     `caption_hide` = crutch words hidden from captions only; remove any that matter
     (e.g. a self-correction like "I mean, lunch" is a joke, keep it).
   - **Music:** `"music": "track.mp3"` if they want it and the track is in Music/.
   - `extra_zooms` (`{"start", "end"}`, optional `"amount"`) for emphasis moments.
3. **Render:** `python3 SKILL/scripts/render.py ".work/<video>/plan.json"`
   (`--preview` = fast half-size draft for long videos or big changes).
4. **Check your work** - the render prints a FACE CHECK and writes:
   - `check.jpg` (8 frames): readable captions, hook showing, framing right?
   - CUT CHECK: every cut must land in silence (it measures the audio). If it lists a cut,
     fix that edge in `keep`/`remove_words` and re-render.
   - `cuts.jpg` (both sides of every cut): no half-words, no awkward jumps, no blinks
     mid-word. If a cut looks wrong, adjust `remove_words`/`keep` and re-render.
   - If FACE CHECK lists problems, fix them (raise `captions.gap_below_chin`, shorten
     the hook, or lower `hook.position_from_top`) and re-render until it says OK.
   Never hand over a video that failed the face check without telling them why.
5. **Report** in 2 to 4 lines: where the file is, old length -> new length, the hook
   used, what you cut (takes, fillers), anything they should know (e.g. "film with a
   bit more room under your chin"). Offer one useful next step.

## 4. Notes

Notes are how the editor gets good. For each note:
- Fix it in this video, re-render, confirm.
- If it should apply to future videos ("always", "never", "I like", "too slow") or they
  say "add this to my rules", change `Rules/style.json` (`overrides`) or `rules.json` AND
  add a dated line under "My notes" in `RULES.md`. When unsure, ask: "Just this video,
  or every video from now on?"
- Mappings: bigger/smaller captions -> `overrides.captions.size`; captions too close to
  the chin -> `gap_below_chin`; more/fewer words -> `words_per_caption`; caps or not ->
  `uppercase`; different highlight -> `highlight_color`; boxes -> `box_color`; whole
  phrase vs word-by-word -> `mode` ("line" or "word"); bigger hook -> `hook.size`;
  tighter/looser pacing -> `rules.cutting.max_pause` (0.15 tight, 0.4 relaxed) and
  `cut_between_sentences`; clipped word endings -> raise `pad_after_word`; no zooms ->
  `zoom.punch_in_on_cuts: false`; stronger punch-ins -> `zoom.amount`; music too loud ->
  `audio.music_volume`; no sound effects -> `audio.sound_effects: false`.
- "Try a different look" -> change `look`, keep their overrides unless they clash.

## 5. Hook testing (several versions of one video)

1. Prepare and review the video once as above.
2. Write N different hooks (curiosity, result, pain, contrarian, number), or use theirs.
3. For each, copy `plan.json` to `plan-hook1.json`, ... with a different `hook.text` and
   `output` (`Finished/<video> - hook 1.mp4`, ...), and render each. The clips are
   re-cut each time, so this takes a few minutes per version.
4. Give a short table: version, hook text, file. Suggest posting them as Instagram trial
   reels and comparing views, follows and leads; save the winning hook style to STYLE.md.

## How the layout works (so you can explain it)

- Captions sit on one steady line just under the lowest point the chin reaches, across
  the chest, never lower than the Instagram safe zone (bottom ~430px).
- The hook is a static title from the very first frame, at the top over hair/cap, and
  hard-cuts off. If the face is too high for it, it takes the captions' spot instead and
  the captions wait.
- Callouts go at the top when there's room, otherwise they briefly replace the captions.
- Punch-ins alternate clip by clip, centered on the face, cut from the full-resolution
  original so they stay sharp.

## Troubleshooting

- A script fails: read the error, fix the cause (missing install, wrong path, bad JSON
  in plan.json), rerun. Don't make the user debug.
- "ffmpeg not found" on Windows right after installing: open a new terminal.
- Transcription slow: `transcription.model` to `base.en`. Inaccurate: `medium.en`.
  Not English: `small` or `large-v3` and set `language` (or `null` to detect).
- Face not found (side profile, very dark): framing falls back to center; tell them.
- Out of disk space: delete old `.work/` folders (working files only, never Raw or
  Finished without asking).

## Don't

- Never delete or overwrite anything in `Raw/`. Never overwrite a finished video they
  already posted; add " v2" to the name instead.
- Never use music they don't have rights to. Only use files in `Music/`.
- Never change their words in captions beyond fixing transcription mistakes.
