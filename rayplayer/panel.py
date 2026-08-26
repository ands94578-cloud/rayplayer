"""Panel configuration: who is in the room and which model each of them is."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import providers, voices


@dataclass
class Speaker:
    name: str
    provider_spec: dict[str, Any]
    stance: str | None = None
    is_host: bool = False
    temperature: float = 0.8
    max_words: int = 90
    max_tokens: int = 600
    voice_spec: dict[str, Any] = field(default_factory=dict)
    provider: providers.Provider | None = field(default=None, repr=False)
    voice: voices.Voice | None = field(default=None, repr=False)

    @property
    def model(self) -> str:
        return self.provider_spec.get("model", "?")

    @property
    def kind(self) -> str:
        return self.provider_spec.get("kind", "?")


@dataclass
class Panel:
    show: str
    language: str
    speakers: list[Speaker]
    host: Speaker | None
    policy: str = "round-robin"

    @property
    def everyone(self) -> list[Speaker]:
        return ([self.host] if self.host else []) + self.speakers


def _speaker(raw: dict[str, Any], defaults: dict[str, Any], offline: bool, is_host: bool) -> Speaker:
    spec = dict(raw.get("provider") or {})
    # A seat's voice is configured independently of its model: nothing says the
    # seat holding GPT has to be voiced by OpenAI.
    voice_spec = {**(defaults.get("voice_defaults") or {}), **(raw.get("voice") or {})}
    sp = Speaker(
        name=raw["name"],
        provider_spec=spec,
        stance=raw.get("stance"),
        is_host=is_host,
        temperature=float(raw.get("temperature", defaults.get("temperature", 0.8))),
        max_words=int(raw.get("max_words", defaults.get("max_words", 90))),
        max_tokens=int(raw.get("max_tokens", defaults.get("max_tokens", 600))),
        voice_spec=voice_spec,
    )
    sp.provider = providers.build(spec, offline=offline)
    return sp


def attach_voices(panel: Panel, offline: bool = False) -> Panel:
    """Built separately from the text providers: recording a transcript should
    not require a TTS key, and re-rendering audio should not re-run the panel."""
    for s in panel.everyone:
        if not s.voice_spec and not offline:
            raise ValueError(f"speaker {s.name!r} has no voice configured; add a \"voice\" block or use --offline-voices")
        s.voice = voices.build(s.voice_spec or {"name": s.name}, offline=offline)
    return panel


def load(path: str | Path, offline: bool = False) -> Panel:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    defaults = {k: raw[k] for k in ("temperature", "max_words", "max_tokens", "voice_defaults") if k in raw}
    speakers = [_speaker(s, defaults, offline, False) for s in raw.get("speakers", [])]
    if len(speakers) < 2:
        raise ValueError("a panel needs at least two speakers -- that is the whole point")
    names = [s.name for s in speakers]
    if len(set(names)) != len(names):
        raise ValueError(f"speaker names must be unique, got {names}")
    host_raw = raw.get("host")
    host = _speaker(host_raw, defaults, offline, True) if host_raw else None
    return Panel(
        show=raw.get("show", "Untitled"),
        language=raw.get("language", "en"),
        speakers=speakers,
        host=host,
        policy=raw.get("policy", "round-robin"),
    )
