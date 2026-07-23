#!/usr/bin/env python3
"""
Generate a transcript locally from a video/audio file using ffmpeg's built-in
whisper filter (whisper.cpp) -- no cloud speech API, no audio leaves the
machine. Requires an ffmpeg build compiled with --enable-whisper (the winget
Gyan.FFmpeg build this skill installs has it) and a local whisper.cpp ggml
model file.

Important limitation: this produces accurate wording and timing, but NOT
speaker attribution -- whisper.cpp doesn't do speaker diarization, so the
output has no speaker names. build_srt.py already handles this fine (the
speaker group in its transcript-line pattern is optional), but articles and
captions downstream won't be able to say "Maria said X" vs "the host said Y"
unless a human identifies who's who afterward.

Usage:
  python transcribe_local.py --source ep.mp4 --model models/ggml-base.en.bin --out transcript.txt

If --model doesn't exist, this script refuses to download it silently and
exits with instructions instead -- fetching a model file is a real download
that needs the user's go-ahead first (filename, source, size), same as any
other download. Once approved and downloaded, the file is reusable across
every future episode.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

MODEL_HELP = """No whisper.cpp model file found at the given --model path.

Before fetching one, ask the user for permission to download it -- state the
filename, source, and size. A reasonable default for English podcasts:

  File:   ggml-base.en.bin  (~148 MB)
  Source: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
  Save to: this skill's scripts/models/ folder

Once downloaded, it's cached and reusable for every future episode -- this
is a one-time setup cost, not a per-episode download."""

SRT_TIME = re.compile(r"(\d+):(\d+):(\d+),(\d+)")


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for name in os.listdir(base):
            if name.lower().startswith("gyan.ffmpeg"):
                for root, _dirs, files in os.walk(os.path.join(base, name)):
                    if "ffmpeg.exe" in files:
                        return os.path.join(root, "ffmpeg.exe")
    print("error: ffmpeg not found on PATH or in the usual winget location", file=sys.stderr)
    sys.exit(1)


def parse_srt(text):
    """Yield (start_seconds, text) for each SRT cue."""
    for block in text.strip().split("\n\n"):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        # first line is a cue index OR the time range itself depending on
        # how whisper.cpp emits it -- handle both
        time_line = lines[1] if SRT_TIME.search(lines[0]) is None else lines[0]
        text_lines = lines[2:] if time_line is lines[1] else lines[1:]
        m = SRT_TIME.match(time_line)
        if not m:
            continue
        h, mi, s, _ms = (int(x) for x in m.groups())
        start = h * 3600 + mi * 60 + s
        cue_text = " ".join(l.strip() for l in text_lines).strip()
        if cue_text:
            yield start, cue_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not os.path.isfile(args.model):
        print(MODEL_HELP, file=sys.stderr)
        sys.exit(1)

    ffmpeg = find_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        # Same issue as cut_clip.py: ffmpeg's filter-option parser splits on
        # ':', which collides with a Windows drive-letter colon in an
        # absolute path. Run from `tmp` and use paths relative to it so
        # neither the model nor the destination path contains one.
        model_rel = os.path.relpath(os.path.abspath(args.model), tmp).replace("\\", "/")
        srt_name = "out.srt"
        source_abs = os.path.abspath(args.source)
        wf = f"whisper=model={model_rel}:language={args.lang}:format=srt:destination={srt_name}"
        cmd = [ffmpeg, "-y", "-i", source_abs, "-vn", "-af", wf, "-f", "null", "-"]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp)
        srt_path = os.path.join(tmp, srt_name)
        if result.returncode != 0:
            print("ffmpeg whisper transcription failed:", file=sys.stderr)
            print(result.stderr[-4000:], file=sys.stderr)
            sys.exit(1)

        if not os.path.isfile(srt_path):
            print("error: whisper filter ran but produced no subtitle file", file=sys.stderr)
            sys.exit(1)

        with open(srt_path, encoding="utf-8") as f:
            srt_text = f.read()

    lines_out = []
    for start_sec, text in parse_srt(srt_text):
        mm, ss = divmod(start_sec, 60)
        lines_out.append(f"{mm}:{ss:02d} {text}")

    if not lines_out:
        print("warning: no speech segments were transcribed -- check the audio track and model", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("Transcript\n\n")
        f.write("\n".join(lines_out) + "\n")

    print(f"wrote {len(lines_out)} transcript lines to {args.out}")
    print("note: no speaker labels -- whisper.cpp doesn't diarize speakers", file=sys.stderr)


if __name__ == "__main__":
    main()
