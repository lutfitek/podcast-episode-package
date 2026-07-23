#!/usr/bin/env python3
"""
Assemble one finished clip from a source video: optional per-segment cropping
(for reframing to whichever speaker is talking), optional burned-in captions,
and an audio fade-in/fade-out. Centralizes the fiddly ffmpeg filtergraph work
so it's written once correctly instead of hand-built inline every time.

Usage (no reframing, just trim + captions + fade):
  python cut_clip.py --source ep.mp4 --start 4:20 --end 6:20 --out clip.mp4 --srt clip.srt

Usage (with active-speaker reframing — segments is a JSON list of
clip-relative {start, end, crop} where crop is "w:h:x:y" in ffmpeg crop-filter
order, e.g. the left half of a 1280x720 split-screen frame is "640:720:0:0"
and the right half is "640:720:640:0"):
  python cut_clip.py --source ep.mp4 --start 4:20 --end 6:20 --out clip.mp4 \
      --srt clip.srt --segments segments.json

segments.json example:
  [
    {"start": 0,    "end": 12.5, "crop": "640:720:0:0"},
    {"start": 12.5, "end": 40.0, "crop": "640:720:640:0"}
  ]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

FADE_DURATION = 0.5


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    # fall back to the winget install location used by this skill's setup step
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        for name in os.listdir(base):
            if name.lower().startswith("gyan.ffmpeg"):
                for root, _dirs, files in os.walk(os.path.join(base, name)):
                    if "ffmpeg.exe" in files:
                        return os.path.join(root, "ffmpeg.exe")
    print("error: ffmpeg not found on PATH or in the usual winget location", file=sys.stderr)
    sys.exit(1)


def parse_timecode(s):
    parts = [int(p) for p in s.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"bad timecode: {s}")


def run(cmd, cwd=None):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr[-4000:], file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--srt", default=None)
    ap.add_argument("--segments", default=None, help="path to a segments JSON file for active-speaker reframing")
    ap.add_argument("--fade", type=float, default=FADE_DURATION)
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    start = parse_timecode(args.start)
    end = parse_timecode(args.end)
    duration = end - start
    if duration <= 0:
        print("error: end must be after start", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        working = os.path.join(tmp, "working.mp4")

        if args.segments:
            with open(args.segments) as f:
                segments = json.load(f)
            parts = []
            for i, seg in enumerate(segments):
                seg_start = start + seg["start"]
                seg_dur = seg["end"] - seg["start"]
                part_path = os.path.join(tmp, f"part{i}.mp4")
                cmd = [
                    ffmpeg, "-y", "-ss", str(seg_start), "-t", str(seg_dur),
                    "-i", args.source,
                    "-vf", f"crop={seg['crop']},scale=1280:720",
                    "-c:v", "libx264", "-c:a", "aac", part_path,
                ]
                run(cmd)
                parts.append(part_path)

            filelist = os.path.join(tmp, "filelist.txt")
            with open(filelist, "w") as f:
                for p in parts:
                    f.write(f"file '{p}'\n")
            run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", filelist, "-c", "copy", working])
        else:
            run([
                ffmpeg, "-y", "-ss", str(start), "-t", str(duration),
                "-i", args.source, "-c:v", "libx264", "-c:a", "aac", working,
            ])

        # final pass: burn captions (if any) + audio fade in/out.
        # Run this step with cwd=tmp and a bare relative filename for the
        # subtitles filter — ffmpeg's subtitles filter uses ':' as its own
        # option separator, which collides with a Windows drive-letter colon
        # (e.g. "C:/Users/...") even when escaped, so the only reliable fix
        # is to avoid putting a colon in that argument at all.
        fade_out_start = max(0, duration - args.fade)
        af = f"afade=t=in:st=0:d={args.fade},afade=t=out:st={fade_out_start}:d={args.fade}"
        vf = None
        if args.srt:
            srt_local = os.path.join(tmp, "captions.srt")
            shutil.copyfile(args.srt, srt_local)
            style = "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF&,BackColour=&H00000000&,BorderStyle=3,Outline=0,Shadow=0"
            vf = f"subtitles=captions.srt:force_style='{style}'"

        out_abs = os.path.abspath(args.out)
        cmd = [ffmpeg, "-y", "-i", os.path.abspath(working)]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-af", af, "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", out_abs]
        run(cmd, cwd=tmp)

    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
