#!/usr/bin/env python3
"""
Build a caption (.srt) file for one clip window from a timestamped transcript,
following the house caption rules (based on a standard accessibility style guide):

  - max 37 characters per line
  - max 2 lines per caption (a 3rd line is allowed only for a leading
    "(Speaker)" cue when the speaker changes)
  - reading speed capped at 3 words/second (180 wpm)
  - minimum 2 seconds on screen per caption
  - sentence case, normal punctuation
  - speaker name in round brackets on its own line at the start of their turn

Transcript input format: lines like "1:20 Maria: text..." or "1:20 Host: text..."
(one paragraph per timestamp — this is the format the skill's transcripts use).
Only lines whose stated timestamp falls within [start, end) are used, plus the
line active at `start` if it started slightly earlier.

Usage:
  python build_srt.py --transcript transcript.txt --start 4:20 --end 6:20 --out clip.srt
"""
import argparse
import re
import sys

LINE_RE = re.compile(r"^(\d+):(\d+)(?::(\d+))?\s+(?:([A-Za-z][A-Za-z .'\-]{0,30}):\s*)?(.*)$")
MAX_CHARS = 37
MAX_LINES = 2
MAX_WPS = 3.0
MIN_DURATION = 2.0


def parse_timecode(s):
    parts = s.strip().split(":")
    parts = [int(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"bad timecode: {s}")


def parse_transcript(path):
    """Parse timestamped dialogue lines, ignoring any leading chapters/show-notes
    block. Transcripts produced by this skill's Step 1-2 (and the format used in
    show notes generally) list chapters as "M:SS Chapter Title" before the actual
    "M:SS Speaker: text" dialogue — both match the same timestamp-prefixed-line
    shape, so without skipping the chapters block first, chapter titles get
    parsed as bogus caption cues. If a line consisting of just "Transcript"
    (optionally with a trailing colon) is found, only lines after it are used;
    otherwise every matching line is used, so plain speaker-only transcripts
    (no chapters section) still work."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    marker_idx = None
    for i, raw in enumerate(lines):
        if raw.strip().rstrip(":").lower() == "transcript":
            marker_idx = i
            break
    if marker_idx is not None:
        lines = lines[marker_idx + 1:]

    entries = []
    for raw in lines:
        m = LINE_RE.match(raw.strip())
        if not m:
            continue
        mm, ss1, ss2, speaker, text = m.groups()
        if ss2 is not None:
            t = int(mm) * 3600 + int(ss1) * 60 + int(ss2)
        else:
            t = int(mm) * 60 + int(ss1)
        text = text.strip()
        if not text:
            continue
        entries.append({"t": t, "speaker": (speaker or "").strip(), "text": text})
    entries.sort(key=lambda e: e["t"])
    return entries


def wrap_words(words, max_chars, max_lines):
    """Greedily pack words into up to max_lines lines of <= max_chars each.
    Returns (lines_used_words, remaining_words)."""
    lines = []
    used = 0
    i = 0
    for _ in range(max_lines):
        if i >= len(words):
            break
        line = ""
        while i < len(words):
            candidate = (line + " " + words[i]).strip()
            if len(candidate) > max_chars:
                break
            line = candidate
            i += 1
        if not line and i < len(words):
            # single word longer than max_chars: take it anyway, truncation is worse
            line = words[i]
            i += 1
        lines.append(line)
    return lines, words[i:]


def build_cues(entries, start, end):
    cues = []
    prev_speaker = None
    for idx, e in enumerate(entries):
        line_start = max(e["t"], start)
        # this paragraph's text runs until the next entry's timestamp, or `end`
        next_t = entries[idx + 1]["t"] if idx + 1 < len(entries) else end
        line_end = min(next_t, end)
        if line_end <= start or e["t"] >= end:
            continue
        available = max(line_end - line_start, MIN_DURATION)

        words = e["text"].split()
        if not words:
            continue

        # cap reading rate: don't cram more words into the available time than 3/sec allows
        max_words_by_rate = max(1, int(available * MAX_WPS))
        speaker_changed = e["speaker"] and e["speaker"] != prev_speaker

        cursor = line_start
        remaining = words
        first_chunk_in_turn = True
        while remaining:
            lines, remaining = wrap_words(remaining, MAX_CHARS, MAX_LINES)
            chunk_word_count = sum(len(l.split()) for l in lines)
            duration = max(MIN_DURATION, chunk_word_count / MAX_WPS)
            cue_end = min(cursor + duration, line_end if not remaining else cursor + duration)

            text_lines = lines[:]
            if speaker_changed and first_chunk_in_turn and e["speaker"]:
                text_lines = [f"({e['speaker']})"] + text_lines

            cues.append({"start": cursor, "end": cue_end, "lines": text_lines})
            cursor = cue_end
            first_chunk_in_turn = False

        prev_speaker = e["speaker"] or prev_speaker

    # clip-relative timing + minimum gap sanity
    for c in cues:
        c["start"] = max(0.0, c["start"] - start)
        c["end"] = max(c["start"] + MIN_DURATION, c["end"] - start)
    return cues


def fmt_srt_time(t):
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, start=1):
            f.write(f"{i}\n")
            f.write(f"{fmt_srt_time(c['start'])} --> {fmt_srt_time(c['end'])}\n")
            f.write("\n".join(c["lines"]) + "\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--start", required=True, help="clip start, e.g. 4:20")
    ap.add_argument("--end", required=True, help="clip end, e.g. 6:20")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    start = parse_timecode(args.start)
    end = parse_timecode(args.end)
    if end <= start:
        print("error: end must be after start", file=sys.stderr)
        sys.exit(1)

    entries = parse_transcript(args.transcript)
    cues = build_cues(entries, start, end)
    if not cues:
        print("warning: no transcript lines found in this window — check timestamps", file=sys.stderr)
    write_srt(cues, args.out)
    print(f"wrote {len(cues)} caption cues to {args.out}")


if __name__ == "__main__":
    main()
