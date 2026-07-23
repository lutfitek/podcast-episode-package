#!/usr/bin/env python3
"""
Render a horizontal clip into a vertical (9:16) social-media template:
dark canvas, a bold title, a bordered 4:3 video box, and burned-in captions
in a high-contrast bar -- stacked top-down with breathing room between each
element (reusing the same caption cues build_srt.py already generates).

Layout follows a specific design spec (not left to whatever fits):
  - the video box is a fixed 4:3 shape, not derived from the source's own
    aspect ratio -- the source is scaled to *cover* the box and center-cropped
    to fill it exactly, so there's never letterboxing inside the box itself.
  - title, box, and caption bar stack top-down with fixed gaps between them
    (TOP_SAFE, then title, then GAP_TITLE_BOX, then the box, then
    GAP_BOX_CAPTION, then the caption) -- not centered or bottom-anchored.
  - TOP_SAFE / BOTTOM_SAFE / SIDE_SAFE keep everything clear of the strips
    platforms like TikTok/Reels/Shorts overlay their own UI chrome onto
    (profile/follow controls up top, caption/engagement icons along the
    bottom and the right edge).

This is a separate export mode from cut_clip.py's horizontal output, not a
replacement for it -- run this on a clip that's already been trimmed (e.g.
by cut_clip.py without --srt, or straight from the source with --start/--end)
if you want both a horizontal and a vertical version of the same moment.

Usage:
  python vertical_social_template.py --source clip.mp4 --srt clip.srt \
      --title "TITLE OF THE VIDEO" --out clip_vertical.mp4

Decorative feed-chrome (a "LIVE" badge, duration counter, social icons) from
the reference mockup this was based on is intentionally NOT included here --
confirm with whoever's using this whether they actually want any of that
burned into the file before adding it; it's more likely meant to show "this
is how it'd look in a feed" than a literal spec.
"""
import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_srt import wrap_words, MAX_CHARS  # noqa: E402

CANVAS_W, CANVAS_H = 1080, 1920

# Platform safe-zone margins: keep title/box/captions clear of where
# TikTok/Reels/Shorts draw their own UI on top of the video. These are
# reasonable defaults based on common creator-tool guidance, not pulled from
# any single platform's exact current spec -- revisit if a specific
# platform's overlay changes.
TOP_SAFE = 220
BOTTOM_SAFE = 280
SIDE_SAFE = 64

GAP_TITLE_BOX = 60      # space between the title block and the video box
GAP_BOX_CAPTION = 50    # space between the video box and the caption bar

BOX_RATIO = 4 / 3  # width:height -- fixed shape, independent of source aspect
BOX_W = CANVAS_W - 2 * SIDE_SAFE
BOX_H = round(BOX_W / BOX_RATIO)
BORDER_PX = 6

TITLE_FONTSIZE = 64
TITLE_LINE_HEIGHT = round(TITLE_FONTSIZE * 1.5)  # heuristic: fontsize -> line box height.
                        # 1.3 measured too tight against Arial Bold's actual
                        # rendered extent in practice -- title text was
                        # nearly touching the box border below it.
TITLE_LINE_SPACING = 12

CAPTION_FONTSIZE = 40  # ~32px in the reference mockup's own coordinate space; this
                        # canvas renders at 1080 wide so scaled up proportionally
CAPTION_LINE_SPACING = 8
CAPTION_BOX_BORDER = 20  # padding between caption text and the edge of its black box

# Defensive cap on how many drawtext filters get chained into one filtergraph.
# NOT because of a hard filter-count ceiling -- see the textfile note below,
# that theory turned out to be wrong -- but very long clips still produce a
# lot of cues, and it's cheap insurance against whatever the real per-instance
# cost turns out to be at scale. Prefer the unmerged 1:1 cues whenever the
# count already fits; this only kicks in for unusually long clips.
MAX_CAPTION_FILTERS = 40
MAX_MERGED_LINES = 4

SRT_CUE_RE = re.compile(
    r"\d+\s*\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.S,
)


def parse_srt(path):
    """Yield (start_seconds, end_seconds, text_with_real_newlines) per cue.

    Rendering captions via drawtext (see main()) rather than the subtitles
    filter sidesteps a real bug found while building this: ffmpeg's subtitles
    filter silently falls back to libass's legacy 384x288 default reference
    resolution when a scale/pad precedes it in the same filter chain, which
    either scales the font up enormously or pushes it off-frame depending on
    MarginV -- neither an explicit `original_size` value fixed it reliably.
    drawtext has no such hidden reference-resolution layer, so font size and
    position map directly and predictably to real output pixels.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()
    for m in SRT_CUE_RE.finditer(content):
        h1, m1, s1, ms1, h2, m2, s2, ms2, text = m.groups()
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if lines:
            yield start, end, "\n".join(lines)


def merge_cues(cues, max_filters=MAX_CAPTION_FILTERS, max_lines=MAX_MERGED_LINES):
    """Collapse a cue list down to at most `max_filters` entries by merging
    consecutive cues into groups, re-wrapping each group's combined words up
    to `max_lines` lines rather than just concatenating raw text (which would
    otherwise overflow the frame width as one unbroken line).

    vertical_social_template.py renders one drawtext filter per cue. An
    earlier version of this function existed to work around what looked like
    a hard ~27-filter crash ceiling on this ffmpeg build -- that turned out
    to be a red herring: bisecting it down showed the crash tracked a single
    cue containing an apostrophe, not filter count at all (see the textfile
    note below for the real fix). MAX_CAPTION_FILTERS is kept as a cheap
    defensive cap for unusually long clips regardless, not because merging
    is known to be necessary now. Merging trades some of the original
    per-cue reading pace for a smaller filtergraph: a merged block covers
    more spoken time and more words than a single build_srt.py cue normally
    would, so treat this as a fallback for long clips, not a style choice --
    prefer the unmerged 1:1 cues whenever the count already fits.

    If a merged group has more words than fit in `max_lines`, the overflow
    is dropped with a trailing "..." rather than silently cut mid-word, and
    a warning is printed so this doesn't fail quietly.
    """
    cues = list(cues)
    if len(cues) <= max_filters:
        return cues

    group_size = math.ceil(len(cues) / max_filters)
    merged = []
    for i in range(0, len(cues), group_size):
        group = cues[i : i + group_size]
        start = group[0][0]
        end = group[-1][1]
        words = " ".join(text.replace("\n", " ") for _, _, text in group).split()

        lines, remaining = [], words
        while remaining and len(lines) < max_lines:
            chunk, remaining = wrap_words(remaining, MAX_CHARS, 1)
            lines.extend(chunk)
        if remaining:
            print(
                f"warning: merged caption block at {start:.1f}s dropped "
                f"{len(remaining)} word(s) that didn't fit in {max_lines} lines",
                file=sys.stderr,
            )
            lines[-1] = lines[-1].rstrip() + " ..."

        merged.append((start, end, "\n".join(lines)))

    return merged


# drawtext's `text=` option needs its value escaped for the ffmpeg filter
# parser (':' and '%' with a backslash), and a literal single quote is a
# real problem: escaping it as \' inside a single-quoted value looks right
# but reliably crashes this ffmpeg build with an access violation instead of
# a parse error (confirmed by bisecting a real caption containing "who's" --
# every other line rendered fine, that one crashed every time, regardless of
# how many other filters were in the chain). Rather than trust a different
# escaping scheme to be safe, every cue's text goes into its own file and is
# passed via drawtext's `textfile=` option instead, which needs no escaping
# at all since ffmpeg just reads the file's raw bytes as the caption text.


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
    print("error: ffmpeg not found", file=sys.stderr)
    sys.exit(1)


def run(cmd, cwd=None):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr[-4000:], file=sys.stderr)
        sys.exit(1)


def find_ffprobe(ffmpeg_path):
    candidate = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
    if os.path.isfile(candidate):
        return candidate
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    print("error: ffprobe not found next to ffmpeg or on PATH", file=sys.stderr)
    sys.exit(1)


def get_duration(ffprobe, source):
    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", source]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return float(result.stdout.strip())


def parse_timecode(s):
    parts = [int(p) for p in s.strip().split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"bad timecode: {s}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--start", default=None, help="trim start, e.g. 4:20 -- omit to use the whole --source file as-is")
    ap.add_argument("--end", default=None, help="trim end, e.g. 6:20 -- required if --start is given")
    ap.add_argument("--srt", default=None)
    ap.add_argument("--title", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bg-color", default="0x1c1c1e")
    ap.add_argument("--fade", type=float, default=0.5, help="audio fade-in/fade-out duration in seconds")
    ap.add_argument("--content-crop", default=None,
                     help="ffmpeg crop spec W:H:X:Y applied to the source BEFORE the "
                          "box-fill step, e.g. to strip out a source's own baked-in "
                          "title header / name-label footer bars before fitting what's "
                          "left into the 4:3 box. Omit to use the full source frame.")
    args = ap.parse_args()

    if bool(args.start) != bool(args.end):
        print("error: --start and --end must be given together", file=sys.stderr)
        sys.exit(1)

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe(ffmpeg)
    box_x = (CANVAS_W - BOX_W) // 2

    # Top-down stack: TOP_SAFE, then the title block (sized to however many
    # lines it actually has), then a gap, then the box. The box's own aspect
    # is fixed at 4:3 regardless of source footage -- scale to *cover* the
    # box (never smaller than it in either dimension) and center-crop the
    # overflow, so the box is always fully filled rather than letterboxed.
    title_lines = args.title.replace("\\n", "\n").count("\n") + 1 if args.title else 0
    title_block_h = (
        title_lines * TITLE_LINE_HEIGHT + max(0, title_lines - 1) * TITLE_LINE_SPACING
        if title_lines
        else 0
    )
    title_y = TOP_SAFE
    box_y = TOP_SAFE + title_block_h + (GAP_TITLE_BOX if title_lines else 0)
    caption_y = box_y + BOX_H + GAP_BOX_CAPTION

    filters = []
    if args.content_crop:
        filters.append(f"crop={args.content_crop}")
    filters += [
        f"scale={BOX_W}:{BOX_H}:force_original_aspect_ratio=increase",
        f"crop={BOX_W}:{BOX_H}",
        f"pad={CANVAS_W}:{CANVAS_H}:{box_x}:{box_y}:color={args.bg_color}",
        f"drawbox=x={box_x - BORDER_PX}:y={box_y - BORDER_PX}:"
        f"w={BOX_W + 2*BORDER_PX}:h={BOX_H + 2*BORDER_PX}:color=white:t={BORDER_PX}",
    ]

    with tempfile.TemporaryDirectory() as tmp:
        if args.title:
            title_text = args.title.replace("\\n", "\n")
            title_path = os.path.join(tmp, "title.txt")
            with open(title_path, "w", encoding="utf-8") as f:
                f.write(title_text)
            filters.append(
                f"drawtext=textfile=title.txt:fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
                f"fontsize={TITLE_FONTSIZE}:fontcolor=white:x=(w-text_w)/2:y={title_y}:"
                f"line_spacing={TITLE_LINE_SPACING}"
            )

        if args.srt:
            cues = merge_cues(list(parse_srt(args.srt)))
            for i, (start, end, text) in enumerate(cues):
                cap_path = os.path.join(tmp, f"cap{i}.txt")
                with open(cap_path, "w", encoding="utf-8") as f:
                    f.write(text)
                filters.append(
                    f"drawtext=textfile=cap{i}.txt:fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
                    f"fontsize={CAPTION_FONTSIZE}:fontcolor=white:x=(w-text_w)/2:y={caption_y}:"
                    f"line_spacing={CAPTION_LINE_SPACING}:box=1:boxcolor=black@1.0:boxborderw={CAPTION_BOX_BORDER}:"
                    f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
                )

        vf = ",".join(filters)
        out_abs = os.path.abspath(args.out)

        cmd = [ffmpeg, "-y"]
        if args.start:
            start_sec = parse_timecode(args.start)
            end_sec = parse_timecode(args.end)
            if end_sec <= start_sec:
                print("error: --end must be after --start", file=sys.stderr)
                sys.exit(1)
            cmd += ["-ss", str(start_sec), "-t", str(end_sec - start_sec)]
        cmd += ["-i", os.path.abspath(args.source)]
        clip_duration = (
            end_sec - start_sec if args.start else get_duration(ffprobe, args.source)
        )
        fade_out_start = max(0, clip_duration - args.fade)
        af = f"afade=t=in:st=0:d={args.fade},afade=t=out:st={fade_out_start}:d={args.fade}"

        cmd += ["-vf", vf, "-af", af, "-c:v", "libx264", "-c:a", "aac",
                "-movflags", "+faststart", out_abs]
        run(cmd, cwd=tmp)

    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
