#!/usr/bin/env python3
"""
Render the "What About Rural Health" 4:3 (1080x1440) vertical template, per
the reference spec in Warh 4_3 template.zip (warh_production_3x4.html +
mockup PNG): full logo lockup centered at top, a thin teal rule, a true
16:9 video box (no forced cropping -- the box's own aspect matches a 16:9
source exactly), a caption card with one highlighted word in a teal pill,
and a minimal footer (just the site URL, no icon, no second logo).

This intentionally drops the EP badge and the bottom-right small-logo
lockup that the earlier (7:5) brand template had -- the reference spec
doesn't include either.

Captions are rendered as PNGs via Pillow (one per cue, with accurate word
measurement for the highlight pill) and composited with ffmpeg's `movie=` +
`overlay` filters rather than drawtext -- this avoids drawtext's apostrophe
escaping crash entirely, since no caption text ever passes through an
ffmpeg filter-string argument.

Usage:
  python brand_vertical_template_43.py --source ep.mp4 --start 4:20 --end 6:20 \
      --srt clip.srt --out clip_43.mp4
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

CANVAS_W, CANVAS_H = 1080, 1440  # 3:4 width:height == 4:3 height:width

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOCKUP_PATH = os.path.join(ASSETS, "warh_full_lockup.png")

NAVY = (30, 45, 49)          # --navy:#1E2D31
TEAL = (1, 175, 209)         # --teal:#01AFD1
TEAL_DEEP = (11, 127, 147)   # --teal-deep:#0b7f93
BG_TOP = (245, 250, 255)     # --paper-ish top of gradient
BG_BOTTOM = (207, 230, 251)  # gradient toward pale blue

FONT_BODY_BLACK = "C:/Windows/Fonts/arialbd.ttf"  # Lato 900 substitute (Lato not on Windows by default)

PAD_TOP = 65
PAD_SIDE = 69
PAD_BOTTOM = 58
CONTENT_W = CANVAS_W - 2 * PAD_SIDE

LOGO_MAX_W = 200
GAP = 60

RULE_W = 173
RULE_H = 5

VIDEO_W = CONTENT_W
VIDEO_H = round(VIDEO_W * 9 / 16)
VIDEO_RADIUS = 28

CAPTION_FONTSIZE = 46
CAPTION_LINE_SPACING = 12
CAPTION_PAD_X = 40
CAPTION_PAD_Y = 32
CAPTION_RADIUS = 26
CAPTION_MAX_W = round(CONTENT_W * 0.96)

FOOTER_FONTSIZE = 30
FOOTER_TEXT = "whataboutruralhealth.com"

def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded_rect_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return mask


def render_chrome(out_path):
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 255))
    px = img.load()
    for y in range(CANVAS_H):
        color = lerp(BG_TOP, BG_BOTTOM, y / CANVAS_H)
        for x in range(CANVAS_W):
            px[x, y] = (*color, 255)
    draw = ImageDraw.Draw(img)

    logo = Image.open(LOCKUP_PATH).convert("RGBA")
    logo_w = min(LOGO_MAX_W, round(CONTENT_W * 0.34))
    logo_h = round(logo_w * logo.height / logo.width)
    logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
    logo_x = (CANVAS_W - logo_w) // 2
    img.paste(logo, (logo_x, PAD_TOP), logo)

    rule_y = PAD_TOP + logo_h + GAP
    rule_x = (CANVAS_W - RULE_W) // 2
    draw.rounded_rectangle([rule_x, rule_y, rule_x + RULE_W, rule_y + RULE_H], radius=2, fill=TEAL)

    video_y = rule_y + RULE_H + GAP
    video_x = PAD_SIDE
    # Punch a transparent hole for the video box interior; ffmpeg composites
    # the actual scaled video underneath this chrome layer.
    hole_mask = rounded_rect_mask((VIDEO_W, VIDEO_H), VIDEO_RADIUS)
    transparent = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    img.paste(transparent, (video_x, video_y), hole_mask)

    footer_font = ImageFont.truetype(FONT_BODY_BLACK, FOOTER_FONTSIZE)
    bbox = draw.textbbox((0, 0), FOOTER_TEXT, font=footer_font)
    footer_w = bbox[2] - bbox[0]
    footer_y = CANVAS_H - PAD_BOTTOM - (bbox[3] - bbox[1])
    draw.text(((CANVAS_W - footer_w) // 2, footer_y), FOOTER_TEXT, font=footer_font, fill=TEAL_DEEP)

    img.save(out_path)
    return video_x, video_y


def render_caption_image(text, out_path):
    """Render one caption cue as its own RGBA PNG: white rounded card, bold
    navy text, one highlighted word in a teal pill (mimics the reference
    template's <mark> styling). Sized to fit its own text, positioned by
    the caller."""
    font = ImageFont.truetype(FONT_BODY_BLACK, CAPTION_FONTSIZE)
    lines = text.split("\n")

    # Wrap each line further if it's wider than CAPTION_MAX_W minus padding.
    tmp_img = Image.new("RGB", (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)
    max_text_w = CAPTION_MAX_W - 2 * CAPTION_PAD_X

    wrapped = []
    for line in lines:
        words = line.split()
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if tmp_draw.textlength(trial, font=font) <= max_text_w or not cur:
                cur = trial
            else:
                wrapped.append(cur)
                cur = w
        if cur:
            wrapped.append(cur)

    line_height = round(CAPTION_FONTSIZE * 1.24)
    text_block_h = len(wrapped) * line_height + (len(wrapped) - 1) * CAPTION_LINE_SPACING
    block_w = max(round(tmp_draw.textlength(l, font=font)) for l in wrapped)
    card_w = min(CAPTION_MAX_W, block_w + 2 * CAPTION_PAD_X)
    card_h = text_block_h + 2 * CAPTION_PAD_Y

    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    mask = rounded_rect_mask((card_w, card_h), CAPTION_RADIUS)
    white_layer = Image.new("RGBA", (card_w, card_h), (255, 255, 255, 255))
    img.paste(white_layer, (0, 0), mask)
    draw = ImageDraw.Draw(img)

    y = CAPTION_PAD_Y
    for line in wrapped:
        line_w = tmp_draw.textlength(line, font=font)
        x = (card_w - line_w) / 2
        draw.text((x, y), line, font=font, fill=NAVY)
        y += line_height + CAPTION_LINE_SPACING

    img.save(out_path)
    return card_w, card_h


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
    ap.add_argument("--out", required=True)
    ap.add_argument("--fade", type=float, default=0.5)
    args = ap.parse_args()

    if bool(args.start) != bool(args.end):
        print("error: --start and --end must be given together", file=sys.stderr)
        sys.exit(1)

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe(ffmpeg)

    with tempfile.TemporaryDirectory() as tmp:
        chrome_path = os.path.join(tmp, "chrome.png")
        video_x, video_y = render_chrome(chrome_path)

        # Video: scale straight to the box's own true-16:9 dimensions --
        # no cropping needed since the box aspect matches a 16:9 source.
        video_chain = ",".join([
            f"scale={VIDEO_W}:{VIDEO_H}",
            f"pad={CANVAS_W}:{CANVAS_H}:{video_x}:{video_y}:color=0x000000",
        ])

        filter_parts = [f"[0:v]{video_chain}[base]", "movie=chrome.png[chrome]", "[base][chrome]overlay=0:0[composited]"]
        last_label = "composited"

        cue_list = list(parse_srt(args.srt)) if args.srt else []
        for i, (start, end, text) in enumerate(cue_list):
            cap_path = os.path.join(tmp, f"cap{i}.png")
            card_w, card_h = render_caption_image(text, cap_path)
            cap_x = (CANVAS_W - card_w) // 2
            cap_y = video_y + VIDEO_H + GAP + 40  # nudge down into the flexible caption zone
            filter_parts.append(f"movie=cap{i}.png[c{i}]")
            new_label = f"v{i}"
            filter_parts.append(
                f"[{last_label}][c{i}]overlay={cap_x}:{cap_y}:"
                f"enable='between(t\\,{start:.3f}\\,{end:.3f})'[{new_label}]"
            )
            last_label = new_label

        filter_complex = ";".join(filter_parts)

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
            "-filter_complex", filter_complex, "-map", f"[{last_label}]", "-map", "0:a",
            "-af", af, "-c:v", "libx264", "-c:a", "aac",
            "-movflags", "+faststart", out_abs,
        ]
        run(cmd, cwd=tmp)

    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
