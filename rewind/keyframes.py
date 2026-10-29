"""Pick the keyframes of a screen recording.

A keyframe is a moment where the screen settles (the user paused) and
looks different from the previous keyframe. Typing and mouse moves change
only a few pixels per frame, so they never count as a settled change on
their own; page loads and dialogs change thousands.

Frames are compared small and in grey (320x180), which is fast and needs
little memory. Chosen keyframes are then saved at full size as PNG.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SMALL = (320, 180)


@dataclass(frozen=True)
class Settings:
    fps: float = 10  # frames per second to compare
    pixel_delta: int = 24  # grey levels a pixel must change to count as changed
    still_pixels: int = 2  # at most this many changed pixels between frames = still
    still_seconds: float = 0.2  # how long the screen must stay still (a fast user rests ~0.3 s on a message)
    min_change: int = 60  # changed pixels vs the last keyframe to keep a new one


@dataclass(frozen=True)
class Keyframe:
    index: int
    time: float  # seconds from the start of the video
    path: Path


def decode_small(video: Path, fps: float) -> np.ndarray:
    """Decode the video as small grey frames: array of shape (n, height, width)."""
    width, height = SMALL
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps={fps},scale={width}:{height}:flags=area,format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width)


def changed_pixels(a: np.ndarray, b: np.ndarray, delta: int) -> int:
    return int((np.abs(a.astype(np.int16) - b.astype(np.int16)) > delta).sum())


def still_runs(frames: np.ndarray, settings: Settings) -> list[tuple[int, int]]:
    """Return (start, end) frame indexes, end exclusive, of every still stretch."""
    min_len = max(2, round(settings.still_seconds * settings.fps))
    runs, start = [], 0
    for i in range(1, len(frames) + 1):
        moving = i == len(frames) or changed_pixels(frames[i - 1], frames[i], settings.pixel_delta) > settings.still_pixels
        if moving:
            if i - start >= min_len:
                runs.append((start, i))
            start = i
    return runs


def pick(frames: np.ndarray, settings: Settings) -> list[int]:
    """Indexes of the keyframes: the middle of each still stretch that differs
    enough from the last keyframe kept."""
    picked: list[int] = []
    for start, end in still_runs(frames, settings):
        middle = (start + end - 1) // 2
        if picked and changed_pixels(frames[picked[-1]], frames[middle], settings.pixel_delta) < settings.min_change:
            continue
        picked.append(middle)
    return picked


def save_frame(video: Path, time: float, path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{time:.3f}", "-i", str(video), "-frames:v", "1", str(path)],
        check=True,
    )


def extract(video: Path, out_dir: Path, settings: Settings = Settings(), max_frames: int | None = None) -> list[Keyframe]:
    """Find the keyframes of a video and save them as PNG in out_dir."""
    frames = decode_small(video, settings.fps)
    indexes = pick(frames, settings)[:max_frames]
    out_dir.mkdir(parents=True, exist_ok=True)
    keyframes = []
    for n, i in enumerate(indexes):
        time = i / settings.fps
        path = out_dir / f"kf_{n:03d}_{time:05.1f}s.png"
        save_frame(video, time, path)
        keyframes.append(Keyframe(index=n, time=round(time, 1), path=path))
    return keyframes
