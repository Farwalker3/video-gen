from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import tempfile
import textwrap
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

import requests
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_audioclips,
    concatenate_videoclips,
)


POSITION_MAP = {
    "top-left": ("left", "top"),
    "top-right": ("right", "top"),
    "bottom-left": ("left", "bottom"),
    "bottom-right": ("right", "bottom"),
    "center": ("center", "center"),
}

DEFAULT_RENDER = {"size": [1280, 720], "fps": 24}
DEFAULT_TRANSITION = 0.85
DEFAULT_DUCK_VOLUME = 0.18
DEFAULT_MUSIC_VOLUME = 0.28
DEFAULT_TTS_ENGINE = "edge-tts"
DEFAULT_TTS_VOICE = "en-US-GuyNeural"
DEFAULT_TTS_PITCH = "-10Hz"
USER_AGENT = "video-gen/1.0 (+https://github.com/Farwalker3/video-gen)"
DOWNLOAD_TIMEOUT = 30


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


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
    digest = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    if not digest:
        digest = "asset"
    return digest[:48]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_http_file(
    url: str,
    out_path: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = DOWNLOAD_TIMEOUT,
) -> Path:
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        request_headers.update(headers)

    with requests.get(url, stream=True, headers=request_headers, timeout=timeout, allow_redirects=True) as response:
        response.raise_for_status()
        with out_path.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    fh.write(chunk)
    return out_path


def download_source(source: str, workdir: Path) -> Path:
    candidate = Path(source)
    if candidate.exists():
        return candidate.resolve()

    if not is_url(source):
        raise FileNotFoundError(f"Source not found: {source}")

    ensure_dir(workdir)
    base_name = slugify(source)

    if "commons.wikimedia.org/wiki/File:" in source:
        filename = source.split("File:", 1)[1].split("?", 1)[0].split("#", 1)[0]
        direct_url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(filename)}"
        out_path = workdir / f"{base_name}{Path(filename).suffix or '.bin'}"
        try:
            return download_http_file(
                direct_url,
                out_path,
                headers={"Referer": "https://commons.wikimedia.org/"},
            )
        except Exception:
            pass

    if is_gdrive_url(source):
        import gdown

        normalized_source = normalize_gdrive_url(source)
        out_path = workdir / f"{base_name}.mp4"
        downloaded = gdown.download(url=normalized_source, output=str(out_path), quiet=True, fuzzy=True)
        if downloaded:
            return Path(downloaded)
        raise RuntimeError(f"Google Drive download failed for {source}")

    # Generic fallbacks: yt-dlp can handle many webpage-based sources such as
    # SoundCloud, Wikimedia Commons file pages, and stock-media landing pages.
    try:
        import yt_dlp

        outtmpl = str(workdir / f"{base_name}.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": "bv*+ba/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=True)
            prepared = Path(ydl.prepare_filename(info))
            if prepared.exists():
                return prepared
            for ext in ("mp4", "mkv", "webm", "mov", "m4v", "mp3", "m4a", "ogg", "oga", "opus", "wav"):
                alt = prepared.with_suffix(f".{ext}")
                if alt.exists():
                    return alt
            matches = sorted(workdir.glob(f"{base_name}.*"))
            if matches:
                return matches[0]
    except Exception:
        pass

    suffix = Path(urllib.parse.urlparse(source).path).suffix or ".bin"
    out_path = workdir / f"{base_name}{suffix}"
    return download_http_file(source, out_path)

def normalize_position(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 2:
        return tuple(value)
    if isinstance(value, str):
        return POSITION_MAP.get(value, value)
    return value


def coalesce_ranges(ranges: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((float(start), float(end)) for start, end in ranges if float(end) > float(start))
    if not ordered:
        return []

    merged: list[list[float]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        prev = merged[-1]
        if start <= prev[1]:
            prev[1] = max(prev[1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def build_text_image(entry: dict[str, Any], workdir: Path) -> Path:
    text = str(entry.get("text", ""))
    font_size = int(entry.get("font_size", 44))
    font_color = entry.get("font_color", "#ffffff")
    box_color = entry.get("box_color", "#111827cc")
    padding = int(entry.get("padding", 22))
    radius = int(entry.get("radius", 18))
    line_spacing = int(entry.get("line_spacing", 8))
    font = load_font(font_size)

    dummy = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=line_spacing)
    width = (bbox[2] - bbox[0]) + padding * 2
    height = (bbox[3] - bbox[1]) + padding * 2

    image = Image.new("RGBA", (max(width, 12), max(height, 12)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if box_color:
        draw.rounded_rectangle(
            (0, 0, image.width - 1, image.height - 1),
            radius=radius,
            fill=ImageColor.getcolor(box_color, "RGBA"),
        )
    draw.multiline_text(
        (padding, padding),
        text,
        font=font,
        fill=ImageColor.getcolor(font_color, "RGBA"),
        spacing=line_spacing,
    )

    out_path = workdir / f"text-{slugify(text)}.png"
    image.save(out_path)
    return out_path


def build_browser_window_image(entry: dict[str, Any], workdir: Path) -> Path:
    size = entry.get("size") or [960, 560]
    width = int(size[0])
    height = int(size[1])
    url = str(entry.get("url", ""))
    title = str(entry.get("title", url or "browser window"))
    headline = str(entry.get("headline", title))
    subheadline = str(entry.get("subheadline", ""))
    accent_color = entry.get("accent_color", "#3b82f6")
    panel_color = entry.get("panel_color", "#0b1220")
    page_color = entry.get("page_color", "#111827")
    text_color = entry.get("text_color", "#f8fafc")
    muted_color = entry.get("muted_color", "#cbd5e1")
    accent_fill = ImageColor.getcolor(str(accent_color), "RGBA")
    panel_fill = ImageColor.getcolor(str(panel_color), "RGBA")
    page_fill = ImageColor.getcolor(str(page_color), "RGBA")
    text_fill = ImageColor.getcolor(str(text_color), "RGBA")
    muted_fill = ImageColor.getcolor(str(muted_color), "RGBA")
    chips = [str(item) for item in entry.get("chips", []) or [] if str(item).strip()]
    body_lines = [str(item) for item in entry.get("body_lines", []) or [] if str(item).strip()]
    cards = [card for card in entry.get("cards", []) or [] if isinstance(card, dict)]
    if not cards:
        cards = [
            {"title": "Signal", "body": "Public footprint"},
            {"title": "Proof", "body": "Built and visible"},
            {"title": "Next", "body": "Ready for the next step"},
        ]

    canvas = Image.new("RGBA", (width, height), panel_fill)
    draw = ImageDraw.Draw(canvas)

    shadow = (20, 24, width - 8, height - 8)
    draw.rounded_rectangle(shadow, radius=28, fill=(0, 0, 0, 90))

    outer = (12, 12, width - 20, height - 20)
    draw.rounded_rectangle(outer, radius=28, fill=panel_fill, outline=accent_fill, width=3)

    chrome_h = 52
    chrome = (12, 12, width - 20, 12 + chrome_h)
    draw.rounded_rectangle(chrome, radius=28, fill=(11, 18, 32, 255), outline=accent_fill, width=2)
    draw.rectangle((12, 12 + chrome_h - 18, width - 20, 12 + chrome_h), fill=(11, 18, 32, 255))

    for idx, color in enumerate([(248, 113, 113, 255), (245, 158, 11, 255), (34, 197, 94, 255)]):
        cx = 40 + idx * 22
        cy = 38
        draw.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=color)

    font_title = load_font(26)
    font_url = load_font(18)
    font_head = load_font(40)
    font_sub = load_font(24)
    font_body = load_font(20)
    font_card_title = load_font(22)
    font_card_body = load_font(17)
    font_chip = load_font(16)

    url_bbox = draw.textbbox((0, 0), url, font=font_url)
    pill_w = min(max(url_bbox[2] - url_bbox[0] + 34, 180), width - 180)
    pill_h = 28
    pill_x = width - pill_w - 42
    pill_y = 22
    draw.rounded_rectangle((pill_x, pill_y, pill_x + pill_w, pill_y + pill_h), radius=14, fill=(15, 23, 42, 255), outline=accent_fill, width=1)
    draw.text((pill_x + 16, pill_y + 4), url, font=font_url, fill=text_fill)

    draw.text((82, 18), title, font=font_title, fill=text_fill)

    inner_left = 44
    inner_top = 84
    inner_right = width - 44
    inner_bottom = height - 42
    draw.rounded_rectangle((inner_left, inner_top, inner_right, inner_bottom), radius=24, fill=page_fill)

    hero_w = int((inner_right - inner_left) * 0.56)
    hero_x = inner_left + 34
    hero_y = inner_top + 30
    body_x = hero_x
    body_y = hero_y + 94

    wrapped_head = textwrap.wrap(headline, width=18) or [headline]
    y = hero_y
    for line in wrapped_head[:3]:
        draw.text((hero_x, y), line, font=font_head, fill=text_fill)
        y += 46

    if subheadline:
        sub_lines = textwrap.wrap(subheadline, width=36)
        y += 8
        for line in sub_lines[:3]:
            draw.text((hero_x, y), line, font=font_sub, fill=muted_fill)
            y += 32

    y = body_y
    for line in body_lines[:4]:
        wrapped = textwrap.wrap(line, width=38) or [line]
        for chunk in wrapped[:2]:
            draw.text((body_x, y), chunk, font=font_body, fill=text_fill)
            y += 28
        y += 6

    chip_y = min(height - 166, max(y + 2, inner_top + 206))
    chip_x = hero_x
    for chip in chips[:4]:
        chip_text = f"{chip}"
        bbox = draw.textbbox((0, 0), chip_text, font=font_chip)
        chip_w = bbox[2] - bbox[0] + 28
        chip_h = bbox[3] - bbox[1] + 16
        draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=12, fill=(15, 23, 42, 255), outline=accent_fill, width=1)
        draw.text((chip_x + 14, chip_y + 7), chip_text, font=font_chip, fill=text_fill)
        chip_x += chip_w + 12
        if chip_x > inner_left + hero_w - 40:
            chip_x = hero_x
            chip_y += chip_h + 10

    card_area_x = inner_left + hero_w + 26
    card_area_w = inner_right - card_area_x - 26
    card_top = inner_top + 26
    card_gap = 14
    card_w = card_area_w
    card_h = 94

    for idx, card in enumerate(cards[:3]):
        top = card_top + idx * (card_h + card_gap)
        rect = (card_area_x, top, card_area_x + card_w, top + card_h)
        card_fill = (18, 27, 46, 255) if idx % 2 == 0 else (20, 34, 54, 255)
        draw.rounded_rectangle(rect, radius=20, fill=card_fill, outline=accent_fill, width=1)
        card_title = str(card.get("title", f"Card {idx + 1}"))
        card_body = str(card.get("body", ""))
        draw.text((card_area_x + 16, top + 14), card_title, font=font_card_title, fill=text_fill)
        if card_body:
            body_lines_card = textwrap.wrap(card_body, width=24) or [card_body]
            by = top + 46
            for line in body_lines_card[:2]:
                draw.text((card_area_x + 16, by), line, font=font_card_body, fill=muted_fill)
                by += 22

    footer = str(entry.get("footer", ""))
    if footer:
        footer_bbox = draw.textbbox((0, 0), footer, font=font_card_body)
        draw.text((inner_right - (footer_bbox[2] - footer_bbox[0]) - 24, inner_bottom - 34), footer, font=font_card_body, fill=muted_fill)

    out_path = workdir / f"browser-window-{slugify(url or title)}.png"
    canvas.save(out_path)
    return out_path


def fit_to_frame(clip: Any, size: tuple[int, int]) -> Any:
    target_w, target_h = size
    if clip.w == target_w and clip.h == target_h:
        return clip
    scale = max(target_w / clip.w, target_h / clip.h)
    resized = clip.resize(scale)
    return resized.crop(width=target_w, height=target_h, x_center=resized.w / 2, y_center=resized.h / 2)


def apply_ken_burns(clip: Any, effect: dict[str, Any], size: tuple[int, int]) -> Any:
    start_zoom = float(effect.get("zoom_start", effect.get("zoom", 1.0)))
    end_zoom = float(effect.get("zoom_end", effect.get("zoom", 1.08)))
    start_x = float(effect.get("pan_start_x", 0.5))
    start_y = float(effect.get("pan_start_y", 0.5))
    end_x = float(effect.get("pan_end_x", start_x))
    end_y = float(effect.get("pan_end_y", start_y))
    width, height = size

    base = fit_to_frame(clip, size)

    def transform(get_frame, t):
        progress = 0.0 if base.duration == 0 else min(max(t / base.duration, 0.0), 1.0)
        zoom = start_zoom + (end_zoom - start_zoom) * progress
        center_x = start_x + (end_x - start_x) * progress
        center_y = start_y + (end_y - start_y) * progress
        frame = get_frame(t)
        image = Image.fromarray(frame)
        scaled_w = max(width, int(width * zoom))
        scaled_h = max(height, int(height * zoom))
        image = image.resize((scaled_w, scaled_h), Image.LANCZOS)
        max_x = max(0, scaled_w - width)
        max_y = max(0, scaled_h - height)
        x = int(max_x * min(max(center_x, 0.0), 1.0))
        y = int(max_y * min(max(center_y, 0.0), 1.0))
        crop = image.crop((x, y, x + width, y + height))
        return np.array(crop)

    return base.fl(transform)


def parse_duration(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def source_to_video_clip(entry: dict[str, Any], workdir: Path, render_size: tuple[int, int] | None) -> Any:
    source = entry["source"]
    clip_path = download_source(source, workdir)
    clip = VideoFileClip(str(clip_path))

    trim_start = float(entry.get("trim_start", 0) or 0)
    trim_end = entry.get("trim_end")
    if trim_start or trim_end is not None:
        clip = clip.subclip(trim_start, float(trim_end) if trim_end is not None else clip.duration)

    target_size = render_size or (clip.w, clip.h)
    if entry.get("ken_burns"):
        clip = apply_ken_burns(clip, dict(entry["ken_burns"]), target_size)
    elif render_size:
        clip = fit_to_frame(clip, target_size)

    if entry.get("volume") is not None and clip.audio is not None:
        clip = clip.volumex(float(entry["volume"]))

    duration = parse_duration(entry.get("duration"))
    if duration is not None and duration < clip.duration:
        clip = clip.subclip(0, duration)

    return clip


def build_overlay_clip(entry: dict[str, Any], workdir: Path) -> Any:
    overlay_type = entry.get("type", "text")
    start = float(entry.get("start", 0) or 0)
    duration = parse_duration(entry.get("duration"))
    position = normalize_position(entry.get("position", "center"))
    fade_in = float(entry.get("fade_in", entry.get("animation", {}).get("fade_in", 0) if isinstance(entry.get("animation"), dict) else 0) or 0)
    fade_out = float(entry.get("fade_out", entry.get("animation", {}).get("fade_out", 0) if isinstance(entry.get("animation"), dict) else 0) or 0)

    if overlay_type == "browser_window":
        image_path = build_browser_window_image(entry, workdir)
        clip = ImageClip(str(image_path)).set_start(start).set_position(position)
        clip = clip.set_duration(duration or float(entry.get("default_duration", 5)))
        if entry.get("size"):
            clip = clip.resize(newsize=tuple(entry["size"]))
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        if fade_in:
            clip = clip.fadein(fade_in)
        if fade_out:
            clip = clip.fadeout(fade_out)
        return clip

    if overlay_type == "text":
        image_path = build_text_image(entry, workdir)
        clip = ImageClip(str(image_path)).set_start(start).set_position(position)
        clip = clip.set_duration(duration or float(entry.get("default_duration", 3)))
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        if fade_in:
            clip = clip.fadein(fade_in)
        if fade_out:
            clip = clip.fadeout(fade_out)
        return clip

    if overlay_type == "image":
        source = download_source(entry["source"], workdir)
        clip = ImageClip(str(source)).set_start(start).set_position(position)
        clip = clip.set_duration(duration or float(entry.get("default_duration", 3)))
        if entry.get("size"):
            clip = clip.resize(newsize=tuple(entry["size"]))
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        if fade_in:
            clip = clip.fadein(fade_in)
        if fade_out:
            clip = clip.fadeout(fade_out)
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
            clip = clip.subclip(0, min(duration, clip.duration))
        clip = clip.set_start(start).set_position(position).set_audio(None)
        if entry.get("opacity") is not None:
            clip = clip.set_opacity(float(entry["opacity"]))
        if fade_in:
            clip = clip.fadein(fade_in)
        if fade_out:
            clip = clip.fadeout(fade_out)
        return clip

    raise ValueError(f"Unsupported overlay type: {overlay_type}")


async def synthesize_speech(text: str, output_path: Path, engine: str, voice: str, lang: str, rate: str, pitch: str) -> Path:
    engine = (engine or DEFAULT_TTS_ENGINE).lower()
    if engine == "edge-tts":
        candidates = ["edge-tts", "gtts"]
    elif engine == "gtts":
        candidates = ["gtts", "edge-tts"]
    else:
        candidates = [engine, "edge-tts", "gtts"]

    errors: list[str] = []
    for candidate in candidates:
        try:
            if candidate == "edge-tts":
                import edge_tts

                communicate = edge_tts.Communicate(text=text, voice=voice or DEFAULT_TTS_VOICE, rate=rate or "+0%", pitch=pitch or DEFAULT_TTS_PITCH)
                await communicate.save(str(output_path))
                return output_path

            if candidate == "gtts":
                from gtts import gTTS

                tts = gTTS(text=text, lang=lang or "en", slow=False)
                tts.save(str(output_path))
                return output_path

            errors.append(f"unsupported TTS engine: {candidate}")
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")

    raise RuntimeError(f"All TTS providers failed: {'; '.join(errors)}")


def build_voiceover_layers(config: dict[str, Any], workdir: Path) -> tuple[list[Any], list[tuple[float, float]]]:
    voiceover_cfg = config.get("audio", {}).get("voiceover") or config.get("voiceover") or {}
    if not voiceover_cfg:
        return [], []

    engine = voiceover_cfg.get("engine", DEFAULT_TTS_ENGINE)
    voice = voiceover_cfg.get("voice", DEFAULT_TTS_VOICE)
    lang = voiceover_cfg.get("lang", "en")
    rate = voiceover_cfg.get("rate", "+0%")
    pitch = voiceover_cfg.get("pitch", DEFAULT_TTS_PITCH)
    volume = float(voiceover_cfg.get("volume", 1.0))
    default_fade_in = float(voiceover_cfg.get("fade_in", 0.08))
    default_fade_out = float(voiceover_cfg.get("fade_out", 0.15))

    segments = voiceover_cfg.get("segments")
    layers: list[Any] = []
    ranges: list[tuple[float, float]] = []

    async def render_segment(idx: int, segment: dict[str, Any]) -> None:
        text = str(segment.get("text", "")).strip()
        if not text:
            return
        start = float(segment.get("start", 0) or 0)
        duration = parse_duration(segment.get("duration"))
        output = workdir / f"voiceover-{idx:02d}-{slugify(text)}.mp3"
        await synthesize_speech(text, output, segment.get("engine", engine), segment.get("voice", voice), segment.get("lang", lang), segment.get("rate", rate), segment.get("pitch", pitch))
        audio = AudioFileClip(str(output)).volumex(float(segment.get("volume", volume)))
        if duration is not None and audio.duration > duration:
            audio = audio.subclip(0, duration)
        if segment.get("fade_in", default_fade_in):
            audio = audio.audio_fadein(float(segment.get("fade_in", default_fade_in)))
        if segment.get("fade_out", default_fade_out):
            audio = audio.audio_fadeout(float(segment.get("fade_out", default_fade_out)))
        audio = audio.set_start(start)
        layers.append(audio)
        effective_duration = duration if duration is not None else audio.duration
        ranges.append((start, start + effective_duration))

    if isinstance(segments, list) and segments:
        async def runner() -> None:
            for idx, segment in enumerate(segments):
                if isinstance(segment, dict):
                    await render_segment(idx, segment)

        asyncio.run(runner())
        return layers, coalesce_ranges(ranges)

    text = str(voiceover_cfg.get("text", "")).strip()
    if text:
        output = workdir / f"voiceover-{slugify(text)}.mp3"
        asyncio.run(synthesize_speech(text, output, engine, voice, lang, rate, pitch))
        audio = AudioFileClip(str(output)).volumex(volume)
        audio = audio.audio_fadein(default_fade_in).audio_fadeout(default_fade_out)
        start = float(voiceover_cfg.get("start", 0) or 0)
        duration = parse_duration(voiceover_cfg.get("duration"))
        if duration is not None and audio.duration > duration:
            audio = audio.subclip(0, duration)
        audio = audio.set_start(start)
        layers.append(audio)
        effective_duration = duration if duration is not None else audio.duration
        ranges.append((start, start + effective_duration))

    return layers, coalesce_ranges(ranges)


def extend_audio_to_duration(audio: Any, duration: float) -> Any:
    if audio.duration >= duration:
        return audio.subclip(0, duration)
    repeats = max(1, math.ceil(duration / audio.duration))
    copies = [audio] * repeats
    return concatenate_audioclips(copies).subclip(0, duration)


def build_music_layers(config: dict[str, Any], workdir: Path, duration: float, duck_ranges: list[tuple[float, float]]) -> list[Any]:
    audio_cfg = config.get("audio", {})
    music_cfg = audio_cfg.get("music") or config.get("music") or []
    if isinstance(music_cfg, dict):
        music_cfg = [music_cfg]

    layers: list[Any] = []
    for idx, track in enumerate(music_cfg):
        if not isinstance(track, dict) or not track.get("source"):
            continue
        source_path = download_source(str(track["source"]), workdir)
        music = AudioFileClip(str(source_path))
        music = extend_audio_to_duration(music, duration)
        start = float(track.get("start", 0) or 0)
        volume = float(track.get("volume", DEFAULT_MUSIC_VOLUME))
        duck_volume = float(track.get("duck_volume", DEFAULT_DUCK_VOLUME))
        duck_under_voiceover = bool(track.get("duck_under_voiceover", True))
        music = music.set_start(start)

        ranges = coalesce_ranges(duck_ranges) if duck_under_voiceover else []
        cursor = 0.0
        if ranges:
            for range_start, range_end in ranges:
                if range_start > cursor:
                    normal = music.subclip(cursor, range_start).volumex(volume).set_start(start + cursor)
                    layers.append(normal)
                ducked = music.subclip(range_start, range_end).volumex(duck_volume).set_start(start + range_start)
                layers.append(ducked)
                cursor = range_end
            if cursor < duration:
                tail = music.subclip(cursor, duration).volumex(volume).set_start(start + cursor)
                layers.append(tail)
        else:
            layers.append(music.volumex(volume))

    return layers


def build_scene_video(scene: dict[str, Any], workdir: Path, render_size: tuple[int, int], default_transition: float) -> tuple[Any, float, float]:
    clip = source_to_video_clip(scene, workdir, render_size)
    base_duration = parse_duration(scene.get("duration")) or clip.duration
    transition_in = float(scene.get("transition_in", scene.get("transition", {}).get("in", default_transition) if isinstance(scene.get("transition"), dict) else default_transition) or 0)

    target_duration = min(clip.duration, base_duration + transition_in)
    if target_duration < clip.duration:
        clip = clip.subclip(0, target_duration)

    start = float(scene.get("start", 0) or 0) - transition_in
    clip = clip.set_start(max(0.0, start))
    if transition_in > 0:
        clip = clip.crossfadein(transition_in)
    if scene.get("opacity") is not None:
        clip = clip.set_opacity(float(scene["opacity"]))

    return clip, base_duration, transition_in


def build_project_timeline(config: dict[str, Any], workdir: Path) -> tuple[Any, list[Any], list[tuple[float, float]]]:
    render_cfg = config.get("render", {})
    render_size = tuple(render_cfg.get("size", DEFAULT_RENDER["size"]))
    fps = int(render_cfg.get("fps", DEFAULT_RENDER["fps"]))
    default_transition = float(config.get("transitions", {}).get("default_duration", DEFAULT_TRANSITION))

    timeline = config.get("timeline") or {}
    scenes = timeline.get("scenes") if isinstance(timeline.get("scenes"), list) else None
    clips: list[Any] = []
    overlays: list[Any] = []
    scene_ranges: list[tuple[float, float]] = []

    if scenes:
        running_start = 0.0
        for scene in scenes:
            if not isinstance(scene, dict) or not scene.get("source"):
                continue
            scene = dict(scene)
            scene_start = float(scene.get("start", running_start) or running_start)
            scene["start"] = scene_start
            clip, scene_duration, _transition_in = build_scene_video(scene, workdir, render_size, default_transition)
            clips.append(clip)
            scene_ranges.append((scene_start, scene_start + scene_duration))

            for layer in scene.get("overlays", []) or []:
                if isinstance(layer, dict):
                    layer = dict(layer)
                    layer["start"] = float(layer.get("start", 0) or 0) + scene_start
                    overlays.append(build_overlay_clip(layer, workdir))

            running_start = scene_start + scene_duration

        base = CompositeVideoClip(clips, size=render_size).set_duration(max(end for _, end in scene_ranges) if scene_ranges else 0)
    else:
        clip_sources = config.get("clip_sources", [])
        if not clip_sources:
            raise ValueError("Config must include timeline.scenes or clip_sources")

        current_start = 0.0
        for entry in clip_sources:
            if not isinstance(entry, dict) or not entry.get("source"):
                continue
            entry = dict(entry)
            entry.setdefault("start", current_start)
            clip, scene_duration, _transition_in = build_scene_video(entry, workdir, render_size, default_transition)
            clips.append(clip)
            current_start = float(entry["start"]) + scene_duration
            scene_ranges.append((float(entry["start"]), float(entry["start"]) + scene_duration))

        base = CompositeVideoClip(clips, size=render_size).set_duration(max(end for _, end in scene_ranges) if scene_ranges else 0)

        for entry in config.get("overlays", []) or []:
            if isinstance(entry, dict):
                overlays.append(build_overlay_clip(dict(entry), workdir))

    if overlays:
        final = CompositeVideoClip([base, *overlays], size=render_size).set_duration(base.duration)
    else:
        final = base

    audio_layers, voice_ranges = build_voiceover_layers(config, workdir)
    audio_layers.extend(build_music_layers(config, workdir, final.duration, voice_ranges))
    if audio_layers:
        final_audio = CompositeAudioClip(audio_layers).set_duration(final.duration)
        final = final.set_audio(final_audio)

    return final, clips, overlays


def ensure_output_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a professional pitch video from JSON config.")
    parser.add_argument("--config", default="example.config.json", help="Path to JSON config file")
    parser.add_argument("--output", default=None, help="Output MP4 path")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_json(config_path)
    output_path = ensure_output_path(Path(args.output or config.get("output", "output/generated-video.mp4")))

    render_cfg = config.get("render", {})
    fps = int(render_cfg.get("fps", DEFAULT_RENDER["fps"]))

    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)
        final, clips, overlays = build_project_timeline(config, workdir)

        export = config.get("export", {})
        final.write_videofile(
            str(output_path),
            fps=fps,
            codec="libx264",
            audio=True,
            audio_codec="aac",
            preset=export.get("preset", "slow"),
            threads=int(export.get("threads", max(2, (os.cpu_count() or 4) - 1))),
            audio_bitrate=export.get("audio_bitrate", "320k"),
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart", "-crf", str(export.get("crf", 18))],
            temp_audiofile=str(workdir / "temp-audio.m4a"),
            remove_temp=True,
        )

        final.close()
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
        for overlay in overlays:
            try:
                overlay.close()
            except Exception:
                pass

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
