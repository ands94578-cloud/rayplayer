"""Voice adapters.

Every adapter returns raw PCM16 mono at a stated sample rate, which is what
lets a WAV be stitched together with the stdlib and no ffmpeg in the loop.
A seat's voice is configured separately from its model on purpose: there is no
reason the seat holding GPT has to be voiced by OpenAI.
"""

from __future__ import annotations

import base64
import math
import zlib
import os
import re
import struct
from dataclasses import dataclass
from typing import Any

from .http import HttpError, post_bytes, post_json


class VoiceError(RuntimeError):
    pass


@dataclass
class Audio:
    pcm: bytes  # signed 16-bit little-endian, mono
    sample_rate: int

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate


class Voice:
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.name = spec.get("name", "")
        self.model = spec.get("model", "")
        self.style = spec.get("style")
        self.timeout = int(spec.get("timeout", 180))
        self.retries = int(spec.get("retries", 3))

    def api_key(self) -> str:
        env = self.spec.get("api_key_env")
        key = os.environ.get(env, "") if env else ""
        if not key:
            raise VoiceError(
                f"{env} is not set -- needed to voice with {self.model or self.name}. "
                f"Use --offline-voices to render with tones instead."
            )
        return key

    def styled(self, text: str) -> str:
        return f"{self.style}: {text}" if self.style else text

    def say(self, text: str) -> Audio:
        raise NotImplementedError


class GeminiVoice(Voice):
    """Prompt-steered TTS. `style` is prepended as a plain-language direction,
    which is the whole reason to pick this one for a panel show."""

    def say(self, text):
        base = self.spec.get("base_url", "https://generativelanguage.googleapis.com")
        body = {
            "contents": [{"parts": [{"text": self.styled(text)}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.name}}},
            },
        }
        try:
            r = post_json(
                f"{base.rstrip('/')}/v1beta/models/{self.model}:generateContent",
                {"x-goog-api-key": self.api_key()},
                body,
                self.timeout,
                self.retries,
            )
        except HttpError as e:
            raise VoiceError(str(e)) from e
        return parse_gemini_audio(r)


def parse_gemini_audio(r: dict[str, Any]) -> Audio:
    """Pulled out of the request so the fiddly part -- base64 plus a sample
    rate that only exists inside the mimeType string -- is testable offline."""
    try:
        part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError, TypeError) as e:
        raise VoiceError(f"no audio in response: {str(r)[:300]}") from e
    mime = part.get("mimeType", "")
    m = re.search(r"rate=(\d+)", mime)
    if not m:
        raise VoiceError(f"cannot read sample rate from mimeType {mime!r}")
    return Audio(base64.b64decode(part["data"]), int(m.group(1)))


class ElevenLabsVoice(Voice):
    """`name` is the voice id from your ElevenLabs voice library, not a
    display name. PCM output keeps stitching dependency-free; note that the
    higher pcm_* rates are a paid-tier feature."""

    def say(self, text):
        base = self.spec.get("base_url", "https://api.elevenlabs.io/v1")
        rate = int(self.spec.get("sample_rate", 24000))
        url = f"{base.rstrip('/')}/text-to-speech/{self.name}?output_format=pcm_{rate}"
        body: dict[str, Any] = {"text": text, "model_id": self.model or "eleven_v3"}
        if self.spec.get("voice_settings"):
            body["voice_settings"] = self.spec["voice_settings"]
        try:
            raw = post_bytes(url, {"xi-api-key": self.api_key(), "accept": "audio/pcm"}, body, self.timeout, self.retries)
        except HttpError as e:
            raise VoiceError(str(e)) from e
        if not raw:
            raise VoiceError(f"empty audio from voice {self.name}")
        return Audio(raw, rate)


class MockVoice(Voice):
    """Offline stand-in: a tone per seat, long enough to match the line. You
    cannot review delivery with it, but you can hear the turn-taking and check
    the stitching, gaps, and cue sheet without paying for audio."""

    def say(self, text):
        rate = int(self.spec.get("sample_rate", 24000))
        seed = zlib.crc32((self.name or self.model or "seat").encode())  # stable across processes
        freq = 180 + (seed % 9) * 45  # distinct pitch per seat
        cjk = len(re.findall(r"[㐀-鿿]", text))
        words = len(re.findall(r"[A-Za-z0-9']+", text)) + cjk * 0.7
        seconds = max(0.6, words / 2.5)
        n = int(rate * seconds)
        fade = int(rate * 0.02)  # keeps concatenation from clicking
        frames = bytearray()
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * freq * i / rate)))
        return Audio(bytes(frames), rate)


REGISTRY = {"gemini": GeminiVoice, "elevenlabs": ElevenLabsVoice, "mock": MockVoice}


def build(spec: dict[str, Any], offline: bool = False) -> Voice:
    kind = "mock" if offline else spec.get("kind", "gemini")
    if kind not in REGISTRY:
        raise VoiceError(f"unknown voice kind {kind!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[kind](spec)
