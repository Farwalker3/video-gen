# video-gen

Python/MoviePy boilerplate for stitching existing clips, adding overlays, and exporting a finished MP4.

This starter is aimed at short-form pitch and teaser videos for The World of Rogues TV Show, with placeholder branding support for Humanoids Now overlays.

What it can do:
- Load existing video clips from local files or remote sources
- Pull clips from YouTube and Google Drive URLs
- Stitch multiple clips together into one timeline
- Add overlays:
  - text
  - images
  - secondary video layers
- Export a final MP4 with H.264 video and AAC audio

Example asset sources included in the sample config:
- raccoon robot footage: https://youtu.be/RoFu0ROP6oU
- The World of Rogues teaser: https://drive.google.com/file/d/1TdZ7SPlPVYXhvWliug9458fKavWceLrB/view?usp=drivesdk

Quick start:
1. Install dependencies with python -m pip install -r requirements.txt
2. Render the sample video with python generate_video.py --config example.config.json --output output/generated-video.mp4
3. Upload the finished MP4 as a workflow artifact with the included GitHub Action

Config format:
- clip_sources: ordered list of clip inputs
- overlays: optional overlay layer definitions
- render: optional size and fps settings

Clip source example:
{
  "source": "https://youtu.be/RoFu0ROP6oU",
  "trim_start": 0,
  "trim_end": 8
}

Text overlay example:
{
  "type": "text",
  "text": "Humanoids Now",
  "start": 0,
  "duration": 4,
  "position": "bottom-right"
}

Secondary video overlay example:
{
  "type": "video",
  "source": "https://drive.google.com/file/d/1TdZ7SPlPVYXhvWliug9458fKavWceLrB/view?usp=drivesdk",
  "start": 5,
  "duration": 6,
  "position": "center",
  "size": [480, 270],
  "opacity": 0.9
}

Notes:
- The workflow installs ffmpeg because MoviePy depends on it.
- Remote downloads use yt-dlp for YouTube and gdown for Google Drive.
- For pitch work, the repo is set up so you can replace the sample sources with your own clips and overlay text.
