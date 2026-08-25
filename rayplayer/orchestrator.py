"""The conversation loop: who talks next, what they see, and what comes back."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Callable

from . import prompts
from .panel import Panel, Speaker
from .providers import MockProvider, Msg, ProviderError

# Heuristic, not a judge model: cheap enough to run on every turn, and its only
# power is to nudge the next speaker. False positives cost one prompt line.
AGREEMENT_OPENERS = re.compile(
    r"^\s*(i (completely |totally |fully )?agree|agreed\b|yes[,.]|exactly\b|absolutely\b|"
    r"that'?s (right|exactly right|fair)|100%|couldn'?t agree|you'?re right|"
    r"(fair|good|great) point|right,|building on|to add to|"
    r"我(完全|也)?同意|同意\b|沒錯|確實|的確|正是|說得對|補充一點)",
    re.IGNORECASE,
)

CJK = re.compile(r"[㐀-鿿豈-﫿぀-ヿ]")
SENTENCE_END = re.compile(r"[.!?。！？…](?=\s|$)")


@dataclass
class Turn:
    index: int
    speaker: str
    role: str  # "host" | "panelist"
    text: str
    model: str
    provider: str
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    nudged: bool = False


@dataclass
class Run:
    show: str
    topic: str
    language: str
    policy: str
    started_at: str
    panel: list[dict] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["turns"] = [asdict(t) for t in self.turns]
        return d


def approx_words(text: str) -> float:
    """Word count that does not pretend Chinese has spaces in it."""
    cjk = len(CJK.findall(text))
    ascii_words = len(re.findall(r"[A-Za-z0-9'’\-]+", text))
    return ascii_words + cjk * 0.7


def clean(text: str, names: list[str]) -> str:
    """Strip the things models add to a spoken line even when told not to."""
    t = text.strip()
    for name in names:
        t = re.sub(rf"^\**{re.escape(name)}\**\s*[:：]\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^[\"“'『「](.*)[\"”'』」]$", r"\1", t.strip(), flags=re.DOTALL)
    t = re.sub(r"^\s*[#>*\-]+\s*", "", t, flags=re.MULTILINE)  # markdown scaffolding
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def soft_trim(text: str, max_words: int) -> str:
    """Cut at a sentence boundary if a turn runs far over. Never mid-sentence:
    a truncated clause reads as a bug in the transcript, a long turn does not."""
    if approx_words(text) <= max_words * 1.6:
        return text
    ends = [m.end() for m in SENTENCE_END.finditer(text)]
    for cut in reversed(ends):
        if approx_words(text[:cut]) <= max_words * 1.3:
            return text[:cut].strip()
    return text


def _history_for(speaker: Speaker, turns: list[Turn]) -> list[Msg]:
    """Everyone else's lines arrive as user turns, tagged with who said them;
    the speaker's own lines come back as assistant turns so the model keeps a
    continuous sense of what it has already committed to."""
    msgs: list[Msg] = []
    for t in turns:
        if t.speaker == speaker.name:
            msgs.append(Msg("assistant", t.text))
        else:
            msgs.append(Msg("user", f"{t.speaker}: {t.text}"))
    return msgs


def _fair_candidates(panel: Panel, turns: list[Turn]) -> list[Speaker]:
    counts = {s.name: 0 for s in panel.speakers}
    for t in turns:
        if t.speaker in counts:
            counts[t.speaker] += 1
    last = turns[-1].speaker if turns else None
    pool = [s for s in panel.speakers if s.name != last] or list(panel.speakers)
    floor = min(counts[s.name] for s in pool)
    # Least-spoken first, so whoever falls back to candidates[0] gets fair
    # rotation for free; the moderator may still reach one turn deeper.
    balanced = [s for s in pool if counts[s.name] <= floor + 1]
    order = {s.name: i for i, s in enumerate(panel.speakers)}
    balanced.sort(key=lambda s: (counts[s.name], order[s.name]))
    return balanced or pool


def _pick_moderated(panel: Panel, turns: list[Turn], topic: str) -> Speaker:
    """Let a model direct the room. Falls back to fair rotation on any trouble --
    a director that fails should cost a shrug, not the recording."""
    candidates = _fair_candidates(panel, turns)
    if len(candidates) == 1:
        return candidates[0]
    director = panel.host or panel.speakers[0]
    if isinstance(director.provider, MockProvider):
        return candidates[0]  # a canned director cannot direct; rotate fairly instead
    names = ", ".join(s.name for s in candidates)
    transcript = "\n\n".join(f"{t.speaker}: {t.text}" for t in turns[-12:])
    try:
        out = director.provider.complete(
            prompts.MODERATOR_CUE.format(names=names),
            [Msg("user", f"Topic: {topic}\n\nTranscript so far:\n\n{transcript}\n\nWho speaks next?")],
            temperature=0.2,
            max_tokens=16,
        )
        said = out.text.strip().strip(".\"'").lower()
        for s in candidates:  # exact answer first -- that is what we asked for
            if said == s.name.lower():
                return s
        for s in candidates:  # then a whole-word mention inside a chattier reply
            if re.search(rf"\b{re.escape(s.name.lower())}\b", said):
                return s
    except ProviderError:
        pass
    return candidates[0]


def next_speaker(panel: Panel, turns: list[Turn], topic: str) -> Speaker:
    if panel.policy == "moderator":
        return _pick_moderated(panel, turns, topic)
    return _fair_candidates(panel, turns)[0]


def agreement_streak(turns: list[Turn]) -> int:
    streak = 0
    for t in reversed(turns):
        if t.role == "host":
            break
        if AGREEMENT_OPENERS.match(t.text):
            streak += 1
        else:
            break
    return streak


def _speak(speaker: Speaker, panel: Panel, topic: str, turns: list[Turn], cue: str | None, index: int) -> Turn:
    system = (
        prompts.host_system(speaker, panel, topic)
        if speaker.is_host
        else prompts.panelist_system(speaker, panel, topic)
    )
    msgs = _history_for(speaker, turns)
    if cue:
        msgs.append(Msg("user", cue))
    out = speaker.provider.complete(system, msgs, speaker.temperature, speaker.max_tokens)
    text = soft_trim(clean(out.text, [s.name for s in panel.everyone]), speaker.max_words)
    return Turn(
        index=index,
        speaker=speaker.name,
        role="host" if speaker.is_host else "panelist",
        text=text,
        model=speaker.model,
        provider=speaker.kind,
        latency_ms=out.latency_ms,
        input_tokens=out.input_tokens,
        output_tokens=out.output_tokens,
        nudged=cue == prompts.DISSENT_CUE,
    )


def record(
    panel: Panel,
    topic: str,
    turns: int,
    host_every: int = 4,
    dissent_after: int = 3,
    on_turn: Callable[[Turn], None] | None = None,
) -> Run:
    """Record one episode. `turns` counts panelist turns; host lines are extra."""
    run = Run(
        show=panel.show,
        topic=topic,
        language=panel.language,
        policy=panel.policy,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        panel=[{"name": s.name, "role": "host" if s.is_host else "panelist", "provider": s.kind, "model": s.model, "stance": s.stance} for s in panel.everyone],
    )
    log: list[Turn] = []
    index = 0
    consecutive_failures = 0

    def emit(t: Turn) -> None:
        log.append(t)
        run.turns.append(t)
        if on_turn:
            on_turn(t)

    if panel.host:
        try:
            emit(_speak(panel.host, panel, topic, log, prompts.OPENING_CUE, index))
            index += 1
        except ProviderError as e:
            run.errors.append(f"host opening failed: {e}")

    spoken = 0
    while spoken < turns:
        if panel.host and spoken and spoken % host_every == 0:
            try:
                emit(_speak(panel.host, panel, topic, log, prompts.INTERJECT_CUE, index))
                index += 1
            except ProviderError as e:
                run.errors.append(f"host interjection failed: {e}")

        speaker = next_speaker(panel, log, topic)
        cue = prompts.DISSENT_CUE if agreement_streak(log) >= dissent_after else None
        try:
            emit(_speak(speaker, panel, topic, log, cue, index))
            index += 1
            spoken += 1
            consecutive_failures = 0
        except ProviderError as e:
            # One lab being down should not end the episode; three in a row means
            # something is wrong with the setup, not with a request.
            run.errors.append(f"turn {index} ({speaker.name}/{speaker.model}) failed: {e}")
            consecutive_failures += 1
            if consecutive_failures >= 3:
                run.errors.append("three consecutive failures -- stopping the recording")
                break
            spoken += 1

    if panel.host:
        try:
            emit(_speak(panel.host, panel, topic, log, prompts.CLOSING_CUE, index))
        except ProviderError as e:
            run.errors.append(f"host closing failed: {e}")
    return run
