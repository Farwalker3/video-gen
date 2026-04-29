from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Iterable, List, Optional

from moviepy.editor import ColorClip, CompositeVideoClip, ImageClip, VideoFileClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont
from yt_dlp import YoutubeDL

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / 'output' / 'final.mp4'
DEFAULT_SOURCES_FILE = ROOT / 'assets' / 'sources.txt'
DOWNLOAD_DIR = ROOT / 'downloads'
TEMP_DIR = ROOT / 'output' / '_temp'


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_url(source: str) -> bool:
    return source.startswith('http://') or source.startswith('https://')


def read_sources(sources_file: Path) -> List[str]:
    if not sources_file.exists():
        return []

    lines: List[str] = []
    for raw in sources_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        lines.append(line)
    return lines


def download_url(source_url: str) -> Path:
    ensure_dir(DOWNLOAD_DIR)
    token = hashlib.sha1(source_url.encode('utf-8')).hexdigest()[:12]
    template = str(DOWNLOAD_DIR / f'clip-{token}.%(ext)s')

    options = {
        'format': 'bv*+ba/best',
        'outtmpl': template,
        'quiet': True,
        'noplaylist': True,
        'merge_output_format': 'mp4',
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(source_url, download=True)
        filename = ydl.prepare_filename(info)
        return Path(filename)


def load_clip(source: str) -> VideoFileClip:
    if is_url(source):
        path = download_url(source)
    else:
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()

    if not path.exists():
        raise FileNotFoundError('Source clip not found: {}'.format(source))

    return VideoFileClip(str(path))


def safe_font(size: int) -> ImageFont.FreeTypeFont:
    font_candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for candidate in font_candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def text_card_image(text: str, subtitle: Optional[str], size: tuple[int, int] = (1280, 720)) -> Path:
    ensure_dir(TEMP_DIR)
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    panel_margin = 70
    panel = [
        panel_margin,
        int(size[1] * 0.18),
        size[0] - panel_margin,
        int(size[1] * 0.82),
    ]
    draw.rounded_rectangle(panel, radius=36, fill=(11, 16, 32, 220), outline=(0, 255, 200, 190), width=4)

    title_font = safe_font(78)
    subtitle_font = safe_font(36)

    title_box = draw.textbbox((0, 0), text, font=title_font)
    title_width = title_box[2] - title_box[0]
    title_height = title_box[3] - title_box[1]
    title_x = (size[0] - title_width) // 2
    title_y = int(size[1] * 0.36) - title_height // 2
    draw.text((title_x + 3, title_y + 3), text, font=title_font, fill=(0, 0, 0, 200))
    draw.text((title_x, title_y), text, font=title_font, fill=(245, 250, 255, 255))

    if subtitle:
        subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
        subtitle_width = subtitle_box[2] - subtitle_box[0]
        subtitle_x = (size[0] - subtitle_width) // 2
        subtitle_y = title_y + title_height + 28
        draw.text((subtitle_x + 2, subtitle_y + 2), subtitle, font=subtitle_font, fill=(0, 0, 0, 170))
        draw.text((subtitle_x, subtitle_y), subtitle, font=subtitle_font, fill=(170, 245, 235, 255))

    out_path = TEMP_DIR / 'brand-card.png'
    image.save(out_path)
    return out_path


def create_text_overlay(text: str, duration: float, subtitle: Optional[str] = None, size: tuple[int, int] = (1280, 720)) -> ImageClip:
    card_path = text_card_image(text=text, subtitle=subtitle, size=size)
    return ImageClip(str(card_path)).set_duration(duration)


def create_image_overlay(image_path: str, duration: float, position: tuple[str, str] = ('right', 'bottom'), width: int = 300) -> ImageClip:
    resolved = Path(image_path).expanduser()
    if not resolved.is_absolute():
        resolved = (ROOT / resolved).resolve()
    return ImageClip(str(resolved)).set_duration(duration).resize(width=width).set_position(position)


def create_video_overlay(source: str, duration: float, position: tuple[str, str] = ('right', 'bottom'), width: int = 340) -> VideoFileClip:
    clip = load_clip(source)
    overlay = clip.subclip(0, min(duration, clip.duration))
    return overlay.resize(width=width).set_position(position)


def build_base_sequence(sources: Iterable[str], fallback_duration: float = 4.0) -> List[VideoFileClip]:
    loaded: List[VideoFileClip] = []
    for source in sources:
        clip = load_clip(source)
        end_time = min(clip.duration, fallback_duration)
        loaded.append(clip.subclip(0, end_time))
    return loaded


def build_placeholder_sequence(title: str) -> List[ColorClip]:
    base = ColorClip(size=(1280, 720), color=(12, 18, 36), duration=4.0)
    return [base]


def render_video(
    sources: List[str],
    output_path: Path,
    title: str,
    subtitle: str,
    overlay_image: Optional[str] = None,
    overlay_video: Optional[str] = None,
) -> None:
    ensure_dir(output_path.parent)

    clips = build_base_sequence(sources)
    if not clips:
        clips = build_placeholder_sequence(title)

    stitched = concatenate_videoclips(clips, method='compose')
    layers = [stitched]
    layers.append(create_text_overlay(title, stitched.duration, subtitle=subtitle, size=stitched.size))

    if overlay_image:
        layers.append(create_image_overlay(overlay_image, stitched.duration))

    if overlay_video:
        layers.append(create_video_overlay(overlay_video, stitched.duration))

    final = CompositeVideoClip(layers, size=stitched.size)
    try:
        final.write_videofile(
            str(output_path),
            codec='libx264',
            audio_codec='aac',
            fps=24,
            temp_audiofile=str(output_path.with_suffix('.temp-audio.m4a')),
            remove_temp=True,
        )
    finally:
        final.close()
        stitched.close()
        for clip in clips:
            clip.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Render a video from multiple clips and overlays.')
    parser.add_argument('--sources-file', default=str(DEFAULT_SOURCES_FILE), help='Path to a newline-delimited list of clip sources.')
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT), help='Output mp4 path.')
    parser.add_argument('--title', default='Humanoids Now', help='Title text for the branded overlay.')
    parser.add_argument('--subtitle', default='Video generation boilerplate with clip stitching and overlays', help='Subtitle for the branded overlay.')
    parser.add_argument('--overlay-image', default=None, help='Optional local image overlay path.')
    parser.add_argument('--overlay-video', default=None, help='Optional local or URL secondary video overlay.')
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources_file = Path(args.sources_file)
    if not sources_file.is_absolute():
        sources_file = (ROOT / sources_file).resolve()

    sources = read_sources(sources_file)
    render_video(
        sources=sources,
        output_path=Path(args.output),
        title=args.title,
        subtitle=args.subtitle,
        overlay_image=args.overlay_image,
        overlay_video=args.overlay_video,
    )
    print('Saved {}'.format(Path(args.output)))


if __name__ == '__main__':
    main()
