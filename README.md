# podcast-episode-package

A [Claude Code](https://claude.com/claude-code) skill that turns a podcast/YouTube
episode into a repeatable content package: topic extraction, data-grounded
articles, a clip timecode table, cut social clips (horizontal + branded
vertical), and Google Docs exports — with local speech-to-text and no audio/video
ever leaving your machine.

See [`SKILL.md`](SKILL.md) for the full step-by-step workflow Claude follows.

## What's in here

```
SKILL.md                              # the skill definition Claude Code loads
scripts/
  transcribe_local.py                 # local Whisper (ffmpeg whisper filter) transcription
  build_srt.py                        # transcript -> caption cues (.srt), house style rules
  cut_clip.py                         # horizontal clip cutter: trim, crop/reframe, captions, audio fade
  vertical_social_template.py         # generic 9:16 vertical clip template
  brand_vertical_template.py          # branded vertical template, 7:5 aspect
  brand_vertical_template_43.py       # branded vertical template, 4:3 aspect (latest)
  assets/                             # logo images used by the branded templates
evals/                                # eval fixtures for testing the skill
```

## Setup

### 1. Install this skill

Copy (or clone) this repo into your Claude Code skills directory:

```bash
git clone https://github.com/lutfitek/podcast-episode-package.git "%USERPROFILE%\.claude\skills\podcast-episode-package"
```

On macOS/Linux the equivalent path is `~/.claude/skills/podcast-episode-package`.

### 2. Install ffmpeg (with whisper support)

Local transcription depends on an ffmpeg build compiled with `--enable-whisper`.
On Windows, the [Gyan.FFmpeg](https://www.gyan.dev/ffmpeg/builds/) winget
package has it:

```bash
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
```

Verify it has whisper support:

```bash
ffmpeg -filters | grep whisper
```

If you're not using Windows/winget, install any ffmpeg build compiled with
`--enable-whisper` and make sure it's on your `PATH`.

### 3. Install Python dependencies

The scripts only need Pillow (used by the branded vertical templates for
compositing logos, cards, and captions):

```bash
pip install Pillow
```

### 4. Download a whisper.cpp model (one-time, only if you need local transcription)

`transcribe_local.py` needs a whisper.cpp ggml model file. It will not
download one automatically — grab it yourself once and reuse it across
episodes:

```
File:   ggml-base.en.bin  (~148 MB)
Source: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
Save to: scripts/models/ggml-base.en.bin
```

That path is git-ignored, so it stays local to your machine.

## Usage

Once the skill is installed, just ask Claude Code to work with a podcast
episode — e.g. "turn this episode into a content package" with a URL, or
"cut me vertical clips from this file" with a local video path. Claude will
follow the steps in [`SKILL.md`](SKILL.md) and call the scripts above as
needed. The scripts can also be run standalone; run any of them with `--help`
for its arguments.

## Notes

- This skill never downloads episode media itself (ToS/copyright reasons) —
  you provide a local file you already have rights to.
- Transcription and clip assembly run entirely locally; only the public-data
  research and Google Docs export steps touch the network.
