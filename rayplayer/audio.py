"""PCM and WAV handling. Stdlib `wave` only -- no ffmpeg required to ship an episode."""

from __future__ import annotations

import wave
from pathlib import Path

SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1


def silence(seconds: float, sample_rate: int) -> bytes:
    return b"\x00" * (int(sample_rate * seconds) * SAMPLE_WIDTH * CHANNELS)


def write_wav(path: str | Path, pcm: bytes, sample_rate: int) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return p


def read_wav(path: str | Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != CHANNELS or w.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"{path}: expected mono 16-bit, got {w.getnchannels()}ch/{w.getsampwidth() * 8}bit")
        return w.readframes(w.getnframes()), w.getframerate()


def stitch(clips: list[tuple[bytes, int]], gap_seconds: float) -> tuple[bytes, int]:
    """Join clips with a gap between them. Mixed sample rates are refused
    rather than silently resampled -- a wrong-speed episode is worse than an
    error message that names the seat to fix."""
    if not clips:
        return b"", 0
    rates = {r for _, r in clips}
    if len(rates) > 1:
        raise ValueError(
            f"clips have different sample rates {sorted(rates)}; "
            f"set the same sample_rate for every voice in the panel"
        )
    rate = clips[0][1]
    gap = silence(gap_seconds, rate)
    return gap.join(pcm for pcm, _ in clips), rate


def timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
