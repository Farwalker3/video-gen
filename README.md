# video-gen

A professional Python/MoviePy workflow for assembling pitch-ready short-form videos from structured JSON.

This repository now supports:

- cross-dissolves between clips
- Ken Burns pan and zoom motion
- animated text overlays and lower thirds
- layered narration, music, and voiceover ducking
- TTS generation for voiceover tracks
- higher-quality h.264 MP4 rendering in GitHub Actions
- Creative Commons / free-media source URLs for music and supplemental visuals

## What it does

- Load clips from local files, Google Drive, or supported media pages
- Stitch multiple scenes into a single timeline with timed transitions
- Add overlays:
  - text
  - images
  - secondary video layers
- Generate voiceover narration with edge-tts or gTTS
- Duck background music automatically when narration is active
- Render a final high-quality MP4 through GitHub Actions and download it as a workflow artifact

## How it works

1. Define scenes, audio layers, and overlays in `example.config.json`
2. Render locally with `generate_video.py`
3. Run the GitHub Actions workflow to build `output/osv-pitch.mp4` and retrieve the `generated-video` artifact

## Quick start

1. Install dependencies with `python -m pip install -r requirements.txt`
2. Render the sample video with `python generate_video.py --config example.config.json --output output/osv-pitch.mp4`
3. Use the GitHub Actions workflow to produce the downloadable artifact

## Config format

Top-level sections:

- `timeline.scenes`: ordered scene definitions with precise start times, durations, transition timing, and per-scene overlays
- `audio.voiceover`: TTS settings plus segment-level narration timing
- `audio.music`: background music layers with ducking controls
- `export`: h.264 render quality settings
- `credits`: attribution reminders for borrowed media

Each scene may define:

- `source`, `trim_start`, `trim_end`
- `duration`
- `transition.in` and `transition.out`
- `ken_burns` with pan and zoom control
- `overlays` for animated lower thirds, labels, or inset media

## Sample pitch map

The sample config is structured around the 90-second OSV pitch:

- 0–10s: Intro / Simulation / YCCC context
- 10–40s: World of Rogues / fox characters / licensed IP / PG-13
- 40–70s: Humanoids Now / bipedal robots / prosthetics
- 70–90s: Fellowship CTA / bridge

## Notes

- The workflow installs ffmpeg because MoviePy depends on it
- Voiceover generation uses edge-tts by default, with gTTS as a fallback option
- The sample config references Creative Commons / free-media pages for music and supplemental visuals
- Pillow is pinned below version 10 for compatibility with the current image handling stack
