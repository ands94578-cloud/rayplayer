"""Turn a recorded transcript into audio.

Runs off the JSON run record rather than the panel loop, so audio can be
re-rendered, partially re-rendered, or re-voiced without spending another
round of text tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import audio as au
from . import panel as panel_mod
from .panel import Panel
from .voices import Audio, VoiceError


def _clip_path(out_dir: Path, index: int, speaker: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in speaker)
    return out_dir / f"turn-{index:03d}-{safe}.wav"


def render(
    run: dict,
    panel: Panel,
    out_dir: str | Path,
    gap_seconds: float = 0.4,
    jobs: int = 1,
    force: bool = False,
    on_clip=None,
) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    seats = {s.name: s for s in panel.everyone}
    turns = run["turns"]

    missing = sorted({t["speaker"] for t in turns} - seats.keys())
    if missing:
        raise ValueError(f"transcript has speakers the panel does not: {missing}")

    errors: list[str] = []

    def synth(item):
        i, turn = item
        path = _clip_path(out, i, turn["speaker"])
        # Skipping finished clips makes a failed render cheap to resume -- audio
        # is the expensive half of this pipeline.
        if path.exists() and path.stat().st_size > 44 and not force:
            return i, path, True
        try:
            clip: Audio = seats[turn["speaker"]].voice.say(turn["text"])
        except VoiceError as e:
            errors.append(f"turn {i} ({turn['speaker']}): {e}")
            return i, None, False
        au.write_wav(path, clip.pcm, clip.sample_rate)
        return i, path, False

    items = list(enumerate(turns))
    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(synth, items))
    else:
        results = [synth(it) for it in items]

    clips: list[tuple[bytes, int]] = []
    cues: list[str] = []
    elapsed = 0.0
    for (i, path, reused), turn in zip(sorted(results), turns):
        if path is None:
            continue
        pcm, rate = au.read_wav(path)
        if clips:
            elapsed += gap_seconds
        cues.append(f"{au.timestamp(elapsed)}  {turn['speaker']} — {turn['text'][:60]}")
        elapsed += len(pcm) / 2 / rate
        clips.append((pcm, rate))
        if on_clip:
            on_clip(i, turn["speaker"], path, reused)

    if not clips:
        raise VoiceError("no audio was produced; " + ("; ".join(errors) or "empty transcript"))

    pcm, rate = au.stitch(clips, gap_seconds)
    episode = au.write_wav(out / "episode.wav", pcm, rate)
    cue_sheet = out / "cues.md"
    header = f"# {run.get('show', 'Episode')} — {run.get('topic', '')}\n\n"
    cue_sheet.write_text(header + "\n".join(cues) + "\n", encoding="utf-8")
    return {
        "episode": episode,
        "cues": cue_sheet,
        "seconds": len(pcm) / 2 / rate,
        "clips": len(clips),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rayplayer.render", description="Render a recorded transcript to audio.")
    p.add_argument("--run", required=True, help="run record JSON written by a recording")
    p.add_argument("--panel", default="panel.json", help="panel config holding the voice for each seat")
    p.add_argument("--out", help="output directory (default: alongside the run record)")
    p.add_argument("--gap", type=float, default=0.4, help="seconds of silence between turns")
    p.add_argument("--jobs", type=int, default=1, help="synthesize N turns concurrently")
    p.add_argument("--force", action="store_true", help="re-synthesize clips that already exist")
    p.add_argument("--offline-voices", action="store_true", help="render tones instead of speech -- no keys, no cost")
    args = p.parse_args(argv)

    run_path = Path(args.run)
    if not run_path.exists():
        print(f"run record not found: {run_path}", file=sys.stderr)
        return 2
    run = json.loads(run_path.read_text(encoding="utf-8"))
    pnl = panel_mod.load(args.panel, offline=True)  # text providers are unused here
    panel_mod.attach_voices(pnl, offline=args.offline_voices)
    out_dir = Path(args.out) if args.out else run_path.with_suffix("")

    def show(i, speaker, path, reused):
        print(f"  {'reused ' if reused else 'voiced '} {i:03d} {speaker:<8} {path.name}")

    try:
        result = render(run, pnl, out_dir, args.gap, args.jobs, args.force, on_clip=show)
    except (ValueError, VoiceError) as e:
        print(f"render failed: {e}", file=sys.stderr)
        return 1
    print(f"\n{result['clips']} clips, {au.timestamp(result['seconds'])} total")
    for e in result["errors"]:
        print(f"! {e}", file=sys.stderr)
    print(f"episode: {result['episode']}")
    print(f"cues:    {result['cues']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
