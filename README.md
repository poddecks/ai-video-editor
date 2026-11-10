# AI Video Editor for Claude

Turn Claude into your own video editor. Drop in a raw talking-head video, and get back a finished vertical reel with:

- 🎯 The best take of every line kept, flubbed takes cut automatically
- ✂️ Tight jump cuts: pauses, breaths and "ums" gone
- 🙂 Face tracking: punch-ins centered on your face, sharp even from 4K
- 💬 Word-by-word captions just under your chin, inside the Instagram safe zone
- 🪝 A designed hook title from the very first frame: three locked styles (Bold, Bubble, Mixed), placed above your head or across your chest automatically
- 🎨 Two designed looks (Clean and Boxed) that you can fully customize
- 📌 Callouts, sound effects, music, and balanced audio
- ✅ A self-check on the final video that proves no text ever covers your face

It runs **on your own computer** using Claude Code. Your videos never get uploaded anywhere, and it learns your style from the notes you give it.

Built for the **Build an AI Video Editor** community.

---

## What you need

| | |
|---|---|
| **Claude** | Any paid plan that includes Claude Code (Pro or higher) |
| **Computer** | Mac or Windows that you can install apps on |
| **Disk space** | 50 GB free recommended. Video files are big. |
| **Python** | 3.9 or newer, plus faster-whisper, OpenCV and Pillow (Claude installs these) |
| **FFmpeg** | Free video engine (Claude installs it in setup) |

---

## Install (2 minutes)

### Easiest: let Claude do it

Open Claude Code and paste this:

> Install the AI Video Editor skill from https://github.com/YOUR-GITHUB-NAME/ai-video-editor into my personal Claude skills folder, then run its setup and fix anything that's missing.

### Manual install

1. Click the green **Code** button above, then **Download ZIP**.
2. Unzip it, and rename the folder to `ai-video-editor` if needed.
3. Move the folder into your Claude skills folder:
   - **Mac:** `~/.claude/skills/` (in Finder: Go → Go to Folder → `~/.claude/skills`)
   - **Windows:** `C:\Users\YOUR NAME\.claude\skills\`
   - Create the `skills` folder if it isn't there.
4. Restart Claude Code and say: **"Set up my AI video editor."**

Setup creates an **AI Editor** folder on your Desktop:

```
AI Editor/
├── Raw/          ← drop videos here
├── Finished/     ← edited videos show up here
├── References/   ← videos and screenshots whose style you love
├── Rules/        ← your style and rules (your editor's brain)
└── Music/        ← background music you own or licensed
```

---

## Use it

Just talk to Claude:

| Say this | What happens |
|---|---|
| "Edit my newest video" | Full edit of the latest video in Raw |
| "Here are 3 reels I love. Learn this style." | Builds your style from references |
| "Captions are too small, and always make them bigger from now on" | Fixes this edit and saves the rule |
| "Make 5 versions of this video with different hooks" | Hook test versions for trial reels |
| "Run the setup test" | Quick check that everything works |

The first edit downloads a speech-recognition model, so it takes a few extra minutes. After that it's quick.

---

## Make it yours

Everything about your editor lives in `AI Editor/Rules/`:

- **style.json** is the look: pick `clean` or `boxed`, then anything in `overrides` wins (caption font, size, colors, highlight, words on screen, hook style, callouts, zoom). Tweak a hook style's color or font under `hook_styles`.
- **rules.json** is the rules: how tight the cuts are, which filler words get cut, output size, loudness, music and sound-effect levels.
- **STYLE.md / RULES.md** are the same thing in plain English, plus the notes you've given over time.

You don't need to edit these by hand. Tell Claude what you want, and it updates them.

**Using your own font:** put the `.ttf` file in `AI Editor/Rules/fonts/` and tell Claude: "Use this font for my captions."

---

## How it works

1. **Transcribe** (`scripts/transcribe.py`): local Whisper speech-to-text with a timestamp on every word.
2. **Plan** (`scripts/plan.py`): finds pauses, fillers and repeated lines, keeps the last good take, and writes an editable `plan.json`.
3. **Review**: Claude reads the transcript like an editor would. It double-checks the takes, writes the hook, picks callouts, and fixes misheard words.
4. **Render** (`scripts/render.py`): finds your face, cuts every clip straight from the original with face-centered punch-ins, lays out captions, hook and callouts around your face, mixes audio, and exports.
5. **Verify** (`scripts/verify.py`): finds your face in the finished video, proves no text covers it, and saves before/after frames of every cut for a final look.

`scripts/edit.py --auto` runs all of it in one shot without the review step.

---

## Troubleshooting

Paste any error into Claude and say "fix this." Common ones:

- **"ffmpeg not found" on Windows right after installing:** close and reopen the terminal or Claude Code.
- **Words getting clipped:** ask Claude to "leave a little more room after words."
- **Transcription is slow:** ask Claude to "use a faster transcription model."

---

## Credits and license

- Code: MIT License (see `LICENSE`)
- Fonts: Poppins (Indian Type Foundry) and Inter (Rasmus Andersson), both SIL Open Font License 1.1 (see `assets/fonts/FONT-LICENSE.md`)
- Face detection: YuNet by Shiqi Yu, MIT License (see `assets/models/LICENSE-yunet`), run with [OpenCV](https://opencv.org)
- Sound effects: generated for this project, free to use
- Transcription: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · Video: [FFmpeg](https://ffmpeg.org)
