"""Generate a bounded WebVTT sprite with FFmpeg, for OSS player skins."""

import base64
import json
import math
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


class StoryboardUnavailable(Exception):
    """The input cannot produce a supported preview within the resource bound."""


def _timestamp(seconds: float) -> str:
    hours, remaining = divmod(round(seconds * 1000), 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    return f"{hours:02}:{minutes:02}:{remaining / 1000:06.3f}"


def generate_storyboard(source: Path) -> dict:
    """Read one local MP4/WebM, returning a sprite and its WebVTT cue map.

    Network protocols and playlist demuxers are not enabled. The caller owns
    authorization, source-size limits, and concurrency. No filenames or user
    metadata are embedded in the response.
    """
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise StoryboardUnavailable()
    input_options = ["-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm"]
    try:
        probe = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                *input_options,
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=duration:stream=width,height",
                "-of",
                "json",
                str(source),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        metadata = json.loads(probe.stdout)
        duration = float(metadata["format"]["duration"])
        stream = metadata["streams"][0]
        if not (
            0 < stream["width"] <= 4096
            and 0 < stream["height"] <= 4096
            and stream["width"] * stream["height"] <= 8_847_360
        ):
            raise StoryboardUnavailable()
        if not math.isfinite(duration) or not 0 < duration <= 21_600:
            raise StoryboardUnavailable()
        frames, columns, width, height = 24, 6, 160, 90
        with TemporaryDirectory(prefix="langboard-storyboard-") as directory:
            sprite = Path(directory) / "storyboard.jpg"
            filters = (
                f"fps=fps={frames / duration}:start_time=0,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"tile={columns}x4:nb_frames={frames}"
            )
            subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-threads",
                    "1",
                    *input_options,
                    "-max_alloc",
                    "67108864",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-filter_threads",
                    "1",
                    "-vf",
                    filters,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "5",
                    str(sprite),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            if not sprite.is_file() or not 0 < sprite.stat().st_size <= 1024 * 1024:
                raise StoryboardUnavailable()
            image = sprite.read_bytes()
        cues = ["WEBVTT\n"]
        for index in range(frames):
            x, y = (index % columns) * width, (index // columns) * height
            cues.append(
                f"{_timestamp(index * duration / frames)} --> {_timestamp((index + 1) * duration / frames)}\n"
                f"storyboard.jpg#xywh={x},{y},{width},{height}\n"
            )
        return {
            "available": True,
            "duration": duration,
            "vtt": "\n".join(cues),
            "sprite_base64": base64.b64encode(image).decode("ascii"),
        }
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, IndexError) as error:
        raise StoryboardUnavailable() from error
