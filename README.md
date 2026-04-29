# video-gen

A lightweight Python/MoviePy workflow for assembling pitch-ready short-form videos from existing assets.

Designed for the World of Rogues and OSV Fellowship context, this repository provides a straightforward path from source clips to a polished MP4 with overlays and branded presentation elements.

## What it does

- Load clips from local files or Google Drive
- Stitch multiple clips into a single timeline
- Add overlays:
  - text
  - images
  - secondary video layers
- Support placeholder Humanoids Now branding elements
- Render a final MP4 through GitHub Actions and download it as a workflow artifact

## How it works

1. Define sources, trims, and overlays in `example.config.json`
2. Render locally with `generate_video.py`
3. Run the GitHub Actions workflow to build `output/generated-video.mp4` and retrieve the `generated-video` artifact

## Quick start

1. Install dependencies with `python -m pip install -r requirements.txt`
2. Render the sample video with `python generate_video.py --config example.config.json --output output/generated-video.mp4`
3. Use the GitHub Actions workflow to produce the downloadable artifact

## Config format

- `clip_sources`: ordered list of clip inputs
- `overlays`: optional overlay layer definitions
- `render`: optional size and fps settings

## Notes

- The workflow installs ffmpeg because MoviePy depends on it
- Remote clips are loaded from Google Drive using gdown
- Pillow is pinned below version 10 for compatibility with the current image handling stack
