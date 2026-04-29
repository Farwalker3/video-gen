from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont
from moviepy.editor import CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips


POSITION_MAP = {
    "top-left": ("left", "top"),
    "top-right": ("right", "top"),
    "bottom-left": ("left", "bottom"),
    "bottom-right": ("right", "bottom"),
    "center": ("center", "center"),
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def is_youtube_url(value: str) -> bool:
    lower = value.lower()
    return "youtu.be" in lower or "youtube.com" in lower


def is_gdrive_url(value: str) -> bool:
    lower = value.lower()
    return "drive.google.com" in lower or "docs.google.com" in lower


def normalize_gdrive_url(source: str) -> str:
    match = re.search(r"/file/d/([^/]+)", source)
    if match:
        return f"https://drive.google.com/uc?id={match.group(1)}"

    parsed = urllib.parse.urlparse(source)
    query = urllib.parse.parse_qs(parsed.query)
    file_id = query.get("id", [None])[0]
    if file_id:
        return f"https://drive.google.com/uc?id={file_id}"

    return source


def slugify(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"asset_{digest}"


def download_source(source: str, workdir: Path) -> Path:
    candidate = Path(source)
    if candidate.exists():
        return candidate.resolve()

    if not is_url(source):
        raise FileNotFoundError(f"Source not found: {source}")

    workdir.mkdir(parents=True, exist_ok=True)
    base_name = slugify(source)

    if is_youtube_url(source):
        import yt_dlp

        outtmpl = str(workdir / f"{base_name}.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": "bv*+ba/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=True)
            prepared = Path(ydl.prepare_filename(info))
            if prepared.exists():
                return prepared
            for ext in ("mp4", "mkv", "webm", "mov", "m4v"):
                alt = prepared.with_suffix(f".{ext}")
                if alt.exists():
                    return alt
            matches = sorted(workdir.glob(f"{base_name}.*"))
            if matches:
                return matches[0]
        raise RuntimeError(f"YouTube download failed for {source}")

    if is_gdrive_url(source):
        import gdown

        normalized_source = normalize_gdrive_url(source)
        out_path = workdir / f"{base_name}.mp4"
        downloaded = gdown.download(url=normalized_source, output=str(out_path), quiet=True, fuzzy=True)
        if downloaded:
            return Path(downloaded)
        raise RuntimeError(f"Google Drive download failed for {source}")

    out_path = workdir / f"{base_name}.mp4"
    urllib.request.urlretrieve(source, out_path)
    return out_path


def normalize_position(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 2:
        return tuple(value)
    if isinstance(value, str):
        return POSITION_MAP.get(value, value)
    return value


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def build_text_overlay(entry: dict[str, Any], workdir: Path) -> Path:
    text = str(entry.get("text", ""))
    font_size = int(entry.get("font_size", 48))
    font_color = entry.get("font_color", "#ffffff")
    box_color = entry.get("box_color", "#111827cc")
    padding = int(entry.get("padding", 24))
    radius = int(entry.get("radius", 18))
    font = load_font(font_size)

    dummy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
    width = (bbox[2] - bbox[0]) + padding * 2
    height = (bbox[3] - bbox[1]) + padding * 2

    image = Image.new("RGBA", (max(width, 8), max(height, 8)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if box_color:
        draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=ImageColor.getcolor(box_color, "RGBA"))
    draw.multiline_text((padding, padding), text, font=font, fill=ImageColor.getcolor(font_color, "RGBA"), spacing=8)

    out_path = workdir / f"text_{slugify(text)}.png"
    image.save(out_path)
    return out_path


def source_to_clip(entry: dict[str, Any], workdir: Path) -> VideoFileClip:
    source = entry["source"]
    clip_path = download_source(source, workdir)
    clip = VideoFileClip(str(clip_path))

    trim_start = float(entry.get("trim_start", 0) or 0)
    trim_end = entry.get("trim_end")
    if trim_start or trim_end is not None:
        clip = clip.subclip(trim_start, float(trim_end) if trim_end is not None else clip.duration)

    if entry.get("size"):
        clip = clip.resize(newsize=tuple(entry["size"]))

    if entry.get("volume") is not None and clip.audio is not None:
        clip = clip.volumex(float(entry["volume"]))

    return clip


def build_overlay_clip(entry: dict[str, Any], workdir: Path) -> Any:
    overlay_type = entry.get("type", "text")
    start = float(entry.get("start", 0) or 0)
    duration = entry.get("duration")
    position = normalize_position(entry.get("position", "center"))

    if overlay_type == "text":
        image_path = build_text_overlay(entry, workdir)
        clip = ImageClip(str(image_path))
        clip = clip.set_start(start).set_duration(float(duration or entry.get("default_duration", 3))).set_position(position)
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        return clip

    if overlay_type == "image":
        source = download_source(entry["source"], workdir)
        clip = ImageClip(str(source)).set_start(start).set_duration(float(duration or entry.get("default_duration", 3))).set_position(position)
        if entry.get("size"):
            clip = clip.resize(newsize=tuple(entry["size"]))
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        return clip

    if overlay_type == "video":
        source = download_source(entry["source"], workdir)
        clip = VideoFileClip(str(source))
        trim_start = float(entry.get("trim_start", 0) or 0)
        trim_end = entry.get("trim_end")
        if trim_start or trim_end is not None:
            clip = clip.subclip(trim_start, float(trim_end) if trim_end is not None else clip.duration)
        if entry.get("size"):
            clip = clip.resize(newsize=tuple(entry["size"]))
        if duration is not None:
            clip = clip.subclip(0, min(float(duration), clip.duration))
        clip = clip.set_start(start).set_position(position).set_audio(None)
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        return clip

    raise ValueError(f"Unsupported overlay type: {overlay_type}")


def ensure_output_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a stitched and overlaid MP4 from JSON config.")
    parser.add_argument("--config", default="example.config.json", help="Path to JSON config file")
    parser.add_argument("--output", default=None, help="Output MP4 path")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_json(config_path)
    output_path = ensure_output_path(Path(args.output or config.get("output", "output/generated-video.mp4")))

    render = config.get("render", {})
    render_size = tuple(render["size"]) if render.get("size") else None
    fps = int(render.get("fps", 24))

    clip_sources = config.get("clip_sources", [])
    if not clip_sources:
        raise ValueError("Config must include at least one clip_sources entry.")

    overlays = config.get("overlays", [])

    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)
        clips = [source_to_clip(dict(entry), workdir) for entry in clip_sources]

        if render_size:
            clips = [clip.resize(newsize=render_size) for clip in clips]

        base = concatenate_videoclips(clips, method="compose")
        overlay_clips = [build_overlay_clip(dict(entry), workdir) for entry in overlays]

        if render_size:
            final = CompositeVideoClip([base, *overlay_clips], size=render_size)
        else:
            final = CompositeVideoClip([base, *overlay_clips], size=base.size)

        final.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            threads=4,
        )

        final.close()
        base.close()
        for clip in clips:
            clip.close()
        for overlay in overlay_clips:
            try:
                overlay.close()
            except Exception:
                pass

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
