#!/usr/bin/env python3
"""
Render the "What About Rural Health" branded vertical template: light
gradient background, logo + podcast label + show name + episode badge up
top, a bordered ~16:9 video box, a caption bar, and a footer with the
Instagram handle and a small logo lockup.

Two-stage pipeline:
  1. Pillow renders a static "chrome" PNG (1080x1920, RGBA) with everything
     except the video and captions -- background, header, box border, footer.
     The video box's interior is left fully transparent so the actual video
     can show through it once composited underneath in stage 2.
  2. ffmpeg scales/cover-crops the source into the box's exact dimensions,
     overlays the chrome PNG on top (its alpha channel does the masking --
     opaque chrome everywhere except the transparent box interior), then
     burns in per-cue captions with drawtext (light box, dark text, matching
     the reference template -- see NOTE below on what's simplified).

NOTE on captions: the reference mockup shows one highlighted word per
caption in a colored pill (karaoke-style emphasis). That needs word-level
timestamps to know which word is "on" at a given instant, which is a bigger
lift than this pipeline currently produces (build_srt.py's cues are
multi-word chunks, not word-level). This renders plain styled captions
(light box, dark navy bold text) without the per-word highlight -- flagging
it here rather than faking it.

Usage:
  python brand_vertical_template.py --source ep.mp4 --start 4:20 --end 6:20 \
      --srt clip.srt --episode 07 --out clip_branded.mp4
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_srt import wrap_words, MAX_CHARS  # noqa: E402

CANVAS_W = 1080
CANVAS_H = round(CANVAS_W * 7 / 5)  # 7:5 vertical aspect (height:width), not the usual 9:16
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ICON_PATH = os.path.join(ASSETS, "warh_icon.png")

# Brand palette, sampled by eye from the reference mockup.
NAVY = (22, 35, 63)
TEAL = (58, 168, 165)
LIGHT_BG_TOP = (245, 249, 253)
LIGHT_BG_BOTTOM = (216, 233, 246)
CAPTION_BG = (255, 255, 255)

FONT_SERIF_BOLD = "C:/Windows/Fonts/georgiab.ttf"
FONT_SANS_BOLD = "C:/Windows/Fonts/arialbd.ttf"

SIDE_MARGIN = 70
HEADER_TOP = 55
ICON_SIZE = 130
BOX_RATIO = 16 / 9
BOX_W = CANVAS_W - 2 * SIDE_MARGIN
BOX_H = round(BOX_W / BOX_RATIO)
BOX_Y = 400
BOX_BORDER = 4
BOX_RADIUS = 28

CAPTION_GAP = 60
CAPTION_PAD_X = 36
CAPTION_PAD_Y = 22
CAPTION_FONTSIZE = 38
CAPTION_RADIUS = 18

FOOTER_Y = CANVAS_H - 150


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_rect_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def render_chrome(episode, out_path):
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    px = img.load()
    for y in range(CANVAS_H):
        t = y / CANVAS_H
        color = lerp(LIGHT_BG_TOP, LIGHT_BG_BOTTOM, t)
        for x in range(CANVAS_W):
            px[x, y] = (*color, 255)

    draw = ImageDraw.Draw(img)

    icon = Image.open(ICON_PATH).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    img.paste(icon, (SIDE_MARGIN, HEADER_TOP), icon)

    label_font = ImageFont.truetype(FONT_SANS_BOLD, 26)
    name_font = ImageFont.truetype(FONT_SERIF_BOLD, 36)
    text_x = SIDE_MARGIN + ICON_SIZE + 30
    draw.text((text_x, HEADER_TOP + 18), "P O D C A S T", font=label_font, fill=TEAL)
    draw.text((text_x, HEADER_TOP + 52), "What About Rural Health?", font=name_font, fill=NAVY)

    badge_font = ImageFont.truetype(FONT_SANS_BOLD, 30)
    badge_text = f"EP · {episode}"
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = (bbox[2] - bbox[0]) + 60
    badge_h = 62
    badge_x = CANVAS_W - SIDE_MARGIN - badge_w
    badge_y = HEADER_TOP + 20
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=badge_h // 2, fill=NAVY,
    )
    draw.text(
        (badge_x + 30, badge_y + (badge_h - (bbox[3] - bbox[1])) // 2 - bbox[1]),
        badge_text, font=badge_font, fill=(255, 255, 255),
    )

    # Video box: draw the teal rounded border, then punch a transparent hole
    # for the interior so the composited video shows through in stage 2.
    box_x = SIDE_MARGIN
    draw.rounded_rectangle(
        [box_x, BOX_Y, box_x + BOX_W, BOX_Y + BOX_H],
        radius=BOX_RADIUS, outline=TEAL, width=BOX_BORDER,
    )
    interior = [box_x + BOX_BORDER, BOX_Y + BOX_BORDER, box_x + BOX_W - BOX_BORDER, BOX_Y + BOX_H - BOX_BORDER]
    hole_mask = rounded_rect_mask(
        (interior[2] - interior[0], interior[3] - interior[1]), max(1, BOX_RADIUS - BOX_BORDER)
    )
    transparent = Image.new("RGBA", hole_mask.size, (0, 0, 0, 0))
    img.paste(transparent, (interior[0], interior[1]), hole_mask)

    # Footer: handle text only (left) -- the Instagram icon glyph was removed
    # per feedback; small logo + name lockup (right) stays.
    ig_font = ImageFont.truetype(FONT_SANS_BOLD, 30)
    draw.text((SIDE_MARGIN, FOOTER_Y + 6), "@whataboutruralhealth", font=ig_font, fill=NAVY)

    small_icon = icon.resize((60, 60), Image.LANCZOS)
    small_name_font = ImageFont.truetype(FONT_SANS_BOLD, 20)
    name_lines = ["What", "About", "Rural", "Health?"]
    name_block_w = max(draw.textbbox((0, 0), l, font=small_name_font)[2] for l in name_lines)
    icon_x = CANVAS_W - SIDE_MARGIN - name_block_w - 60 - 12
    img.paste(small_icon, (icon_x, FOOTER_Y - 5), small_icon)
    for i, line in enumerate(name_lines):
        draw.text((icon_x + 72, FOOTER_Y - 8 + i * 22), line, font=small_name_font, fill=NAVY)

    img.save(out_path)


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


def find_ffprobe(ffmpeg_path):
    candidate = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
    if os.path.isfile(candidate):
        return candidate
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    print("error: ffprobe not found", file=sys.stderr)
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


SRT_CUE_RE = re.compile(
    r"\d+\s*\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.S,
)


def parse_srt(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()
    for m in SRT_CUE_RE.finditer(content):
        h1, m1, s1, ms1, h2, m2, s2, ms2, text = m.groups()
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if lines:
            yield start, end, "\n".join(lines)


def run(cmd, cwd=None):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        print(f"command failed: {' '.join(cmd)}", file=sys.stderr)
        print(result.stderr[-4000:], file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--srt", default=None)
    ap.add_argument("--episode", default="01")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fade", type=float, default=0.5)
    args = ap.parse_args()

    if bool(args.start) != bool(args.end):
        print("error: --start and --end must be given together", file=sys.stderr)
        sys.exit(1)

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe(ffmpeg)
    box_x = SIDE_MARGIN

    with tempfile.TemporaryDirectory() as tmp:
        chrome_path = os.path.join(tmp, "chrome.png")
        render_chrome(args.episode, chrome_path)

        video_chain = ",".join([
            f"scale={BOX_W}:{BOX_H}:force_original_aspect_ratio=increase",
            f"crop={BOX_W}:{BOX_H}",
            f"pad={CANVAS_W}:{CANVAS_H}:{box_x}:{BOX_Y}:color=0x000000",
        ])

        # Captions must be drawn AFTER the chrome overlay, not before it --
        # chrome.png is opaque everywhere except a transparent cutout over
        # the video box, so anything drawn on the base layer underneath it
        # (outside that cutout) is completely hidden once chrome is
        # composited on top. Confirmed this the hard way: captions were
        # rendering successfully but invisible in the output until this was
        # reordered.
        caption_y = BOX_Y + BOX_H + CAPTION_GAP
        caption_filters = []
        if args.srt:
            for i, (start, end, text) in enumerate(parse_srt(args.srt)):
                cap_path = os.path.join(tmp, f"cap{i}.txt")
                with open(cap_path, "w", encoding="utf-8") as f:
                    f.write(text)
                caption_filters.append(
                    f"drawtext=textfile=cap{i}.txt:fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
                    f"fontsize={CAPTION_FONTSIZE}:fontcolor=0x{NAVY[0]:02x}{NAVY[1]:02x}{NAVY[2]:02x}:"
                    f"x=(w-text_w)/2:y={caption_y}:line_spacing=10:"
                    f"box=1:boxcolor=white@1.0:boxborderw={CAPTION_PAD_X}:"
                    f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
                )

        filter_complex = (
            f"[0:v]{video_chain}[base];"
            f"movie=chrome.png[chrome];"
            f"[base][chrome]overlay=0:0[composited]"
        )
        if caption_filters:
            filter_complex += ";[composited]" + ",".join(caption_filters) + "[vout]"
        else:
            filter_complex += ";[composited]copy[vout]"

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

        out_abs = os.path.abspath(args.out)
        cmd += [
            "-filter_complex", filter_complex, "-map", "[vout]", "-map", "0:a",
            "-af", af, "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart", out_abs,
        ]
        run(cmd, cwd=tmp)

    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
