---
name: podcast-episode-package
description: Turn one podcast/YouTube episode into a content package — 5 discussion topics, short paraphrased articles grounded in public data, a 2-minute-clip timecode table anchored to real transcript timestamps, cut mp4 clips, and one Google Doc per article. Use this whenever the user gives a podcast or YouTube episode URL and asks for articles, show notes, a content package, clips, quotes, or timecodes for editing — even if they only ask for one piece of it (e.g. "just pull the topics" or "cut me some clips from this episode"), since the steps below are also useful individually. Also trigger on phrases like "turn this episode into...", "make articles from this podcast", "get quotes and timecodes from...", or a repeat of a workflow the user has run before on a different episode.
---

# Podcast Episode → Content Package

This skill turns one podcast episode into a repeatable content package: topic extraction, short data-grounded articles, a clip-cutting plan, actual cut clips, and Google Docs exports. It exists because this is a workflow worth doing consistently across episodes — every episode should get the same rigor around sourcing quotes and public-data context, not a one-off improvisation.

Default to running the **full pipeline** (topics → articles → clip table → cut clips → Google Docs) unless the user's request is clearly scoped to less (e.g. "just tell me the topics" or "just cut these three clips"). If they've already given you specific timecodes or articles from earlier in the conversation, skip straight to the step that's still outstanding rather than redoing finished work.

Prefer running things locally wherever the step allows it. Transcription (Step 2), caption generation, and clip assembly (Step 5) can all run entirely on the user's machine with no audio or video leaving it — do that by default rather than reaching for a cloud API. Public-data research (Step 3) and Google Docs export (Step 6) inherently need the network and stay that way; "run locally where possible" means local when a local option exists, not local at all costs.

## Inputs you need

- A podcast or YouTube episode URL.
- Optionally, a local video/audio file path — **only if the user already has one.** Never fetch or download the episode yourself; see "Never download media" below.
- Optionally, a target Google Drive folder ID for the Doc exports (default: user's Drive root).

## Step 1 — Extract episode metadata and 5 topics

Fetch the episode page (WebFetch for a static page; the Browser tool for YouTube/YouTube Music, which render client-side). Pull the title, guest name(s), chapter list, and show notes/description. From the chapters and show notes, identify 5 main discussion topics — group adjacent chapters that cover the same theme rather than just listing chapter titles verbatim.

## Step 2 — Get a verified transcript

This step is the integrity backbone of the whole skill: every article and every quote downstream depends on having real words the speaker actually said, not a plausible-sounding paraphrase invented from the topic list.

Try, in order:
1. If the user already pasted a transcript in the conversation, use that — it's already verified.
2. On YouTube, open the video (not YouTube Music — its transcript panel is less reliable), click the description's "...more" expander, scroll past "Explore the podcast" to the "Transcript" section, and click "Show transcript". Wait for it to load; it can take a few seconds. Then use `get_page_text` or `read_page` to pull the cue text.
3. **If there's no transcript online but a local audio/video file exists, generate one locally instead of giving up.** This still counts as "verified" — it's a direct transcription of real audio the speaker actually recorded, not a guess — and it runs entirely on the user's machine:
   - `scripts/transcribe_local.py` calls ffmpeg's built-in `whisper` filter (whisper.cpp), which this skill's ffmpeg install already has compiled in — no audio is sent anywhere.
   - It needs a whisper.cpp model file, cached at `scripts/models/`. If none exists yet, **ask the user for permission before downloading one** (say the filename, source, and size — the script's `MODEL_HELP` text has a ready-made recommendation, `ggml-base.en.bin` at ~148MB from Hugging Face's ggerganov/whisper.cpp). This is a one-time setup cost per machine, not a per-episode download, so it's worth doing once and reusing.
   - Run it: `python scripts/transcribe_local.py --source <local file> --model scripts/models/ggml-base.en.bin --out transcript.txt`
   - **Tell the user the limitation up front**: whisper.cpp transcribes wording and timing accurately but doesn't identify *who* is speaking. If the episode has multiple speakers and it matters which one said what (for framing an article, or for the "(Speaker)" caption convention in Step 5), ask the user to identify who's talking when for the moments that matter, rather than guessing or attributing a paraphrase to the wrong person.
4. If none of the above produce a transcript — no pasted text, no working online transcript, and no local file to transcribe from — **stop and tell the user plainly**: no verified transcript is available, and ask them to paste one. Do not proceed to writing articles or picking quote timecodes from an unverified transcript — that's the difference between reporting what a real person said and inventing dialogue for them.

## Step 3 — Write the articles

For each topic (all 5, per the default full-pipeline scope, unless the user narrowed it): write a short article, roughly 400–450 words, one page.

Structure:
1. **Body first**: paraphrase what the speaker actually said on this topic, using the verified transcript as ground truth. Then add 1–2 paragraphs of public-data context — use WebSearch to find real, current statistics or reporting that corroborates or extends the speaker's point, and cite real source URLs.
2. **Summary paragraph last, placed at the top**: once the body is written, go back and write a 2–3 sentence summary of what the article actually covers, and prepend it. Writing it last keeps it honest — a summary written before the body tends to describe what you *planned* to say rather than what you *actually* said.
3. **Sources list** at the end with real, working URLs.

Quoting rule: you're working from a transcript that is someone else's copyrighted material. Paraphrase the substance of what was said rather than quoting it — across the *entire deliverable* (all articles combined), you may use at most one direct quote, under 15 words, with attribution. This isn't a formatting preference; reproducing more than that risks copyright infringement regardless of how the user phrases the request ("give me quotes for each clip" means "tell me what's said in each window," not "transcribe verbatim passages").

## Step 4 — Build the clip timecode table

For each article's topic, find the moment in the verified transcript where the speaker makes that article's central claim, and give a 2-minute window (extend into an adjacent chapter if the natural moment runs short) anchored to real spoken timestamps from the transcript — never estimated or rounded from chapter markers alone, since chapter markers mark topic boundaries, not the best soundbite within them.

Present as a table: topic | timecode range | one-line paraphrased description of what's said in that window (not a quote — see Step 3's quoting rule, which applies here too).

## Step 5 — Cut the clips (if a local file was provided)

Only do this if the user has given you a local video/audio file path. If they haven't, present the timecode table and ask whether they have a file to cut from — don't ask them to download one, since fetching the episode yourself is off-limits (see below).

Each finished clip needs three things layered on top of the raw trim: an audio fade-in/out, burned-in captions, and — where the footage supports it — reframing so the person currently talking is the one on screen. Two bundled scripts, in this skill's own `scripts/` folder (e.g. `C:\Users\<user>\.claude\skills\podcast-episode-package\scripts\`), do the fiddly parts so you're not hand-building ffmpeg filtergraphs from scratch each time:

- `scripts/build_srt.py` — turns a slice of the transcript into a caption file that follows the house caption rules (based on a standard accessibility captioning style guide): max 37 characters/line, max 2 lines/caption (a "(Speaker)" line is allowed as a 3rd line when the speaker changes), reading speed capped at 3 words/second, minimum 2 seconds on screen per caption, and the speaker's name in round brackets at the start of their turn.
- `scripts/cut_clip.py` — trims the source, optionally crops+concatenates per-speaker segments, burns in the captions (white sans-serif on a black box), and applies the audio fades, in one pass.

1. **Set up ffmpeg.** Check with `ffmpeg -version`. If missing, tell the user you need to install it and ask before proceeding — it's a real change to their system, however routine. Once confirmed: `winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --disable-interactivity`. A fresh install won't be on PATH in the current shell session; `cut_clip.py` already knows how to find it in the usual winget location as a fallback, but if you're calling ffmpeg directly for anything else, locate it under `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe`.
2. **Check whether reframing is possible.** Pull one representative frame from partway through the clip window (`ffmpeg -ss <mid-timestamp> -i <source> -frames:v 1 sample.png`) and look at it (Read the image). If it's a split-screen or multi-box layout where each speaker occupies a fixed, identifiable region of the frame, note the crop rectangle for each speaker as `w:h:x:y` (ffmpeg crop order) — e.g. the left half of a 1280x720 frame is `640:720:0:0`, the right half is `640:720:640:0`. If the layout is a single full-frame shot, a dynamic multi-camera cut, or otherwise not cleanly divisible, skip reframing for this clip — don't guess at crop coordinates from a layout you can't actually see clearly, since a wrong crop is worse than no crop.
3. **Build a segments file (only if reframing).** Using the transcript's speaker labels and timestamps within the clip window, write a small JSON file listing each speaker turn as `{"start": <clip-relative seconds>, "end": <clip-relative seconds>, "crop": "<w:h:x:y>"}` — see the docstring in `cut_clip.py` for the exact shape.
4. **Generate the captions:** `python scripts/build_srt.py --transcript <transcript file or path to pasted transcript saved as a temp file> --start <clip start> --end <clip end> --out clip.srt`
5. **Assemble the clip:** `python scripts/cut_clip.py --source <source file> --start <clip start> --end <clip end> --out <out>.mp4 --srt clip.srt [--segments segments.json]`. Put outputs in a `clips/` subfolder next to the source file, named `topicN_clipM_<short-slug>_<start>_<end>.mp4` so filenames alone tell an editor what's in them and sort in table order.
6. **Sanity-check the captions before calling it done.** Skim the generated `.srt` — the script enforces line length and timing mechanically, but it doesn't spell out numbers one-to-ten or otherwise polish phrasing the way the full style guide asks. A quick pass to fix any caption that reads awkwardly is worth it before handing clips to an editor.
7. Confirm each output file exists and has nonzero size before reporting success.

## Step 6 — Export articles to Google Docs

Use the Drive `create_file` tool: one call per article, `contentMimeType: "text/plain"` (which Drive auto-converts to a native Google Doc), `title` set to the article's headline, and the target folder if the user gave one. Report back the `viewUrl` for each doc as a markdown link.

## Constraints that apply throughout

**Never download media.** Don't use yt-dlp, browser-based video grabbers, or any other tool to fetch audio/video from YouTube or any streaming platform for the user — that's a ToS and likely copyright violation regardless of the user's stated purpose. This skill only *analyzes* a page the user can already view and *edits* a file the user already has. If they don't have a local file yet, tell them to get one through a legitimate channel (YouTube Premium offline download, direct outreach to the show for a raw file, their own screen recording under whatever rights they hold) — the same guidance applies every time this comes up, not just the first. This doesn't extend to the whisper.cpp model file in Step 2 — that's a speech-recognition tool, not episode content, and downloading it still goes through the normal ask-first-and-state-size rule for any download.

**Never fabricate quotes or dialogue.** If Step 2 doesn't produce a verified transcript, don't write articles or pick clip timecodes from guesswork dressed up as paraphrase — stop and ask for a transcript instead. A confident-sounding paraphrase of what someone "probably said" based on show notes is functionally a fabricated quote once it's presented as description of the episode's content.

**Ask before installing software.** ffmpeg (or anything else) goes through the user first, even though it's a routine, reversible install.
