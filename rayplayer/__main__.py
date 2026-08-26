from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import panel as panel_mod
from . import audio as audio_mod
from . import orchestrator, render, transcript
from .providers import ProviderError
from .voices import VoiceError


def slugify(topic: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", topic.strip().lower()).strip("-")
    return (s[:48] or "episode") + "-" + time.strftime("%Y%m%d-%H%M%S")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rayplayer",
        description="Record a panel podcast where each seat is a different lab's model.",
    )
    p.add_argument("--panel", default="panel.json", help="panel config file (default: panel.json)")
    p.add_argument("--topic", required=True, help="what the episode is about")
    p.add_argument("--turns", type=int, default=12, help="panelist turns; host lines are extra")
    p.add_argument("--policy", choices=["round-robin", "moderator"], help="override the panel's turn-taking policy")
    p.add_argument("--host-every", type=int, default=4, help="host steps in every N panelist turns")
    p.add_argument("--dissent-after", type=int, default=3, help="nudge for disagreement after N agreeing turns in a row (0 disables)")
    p.add_argument("--out", default="out", help="output directory")
    p.add_argument("--audio", action="store_true", help="render the episode to audio after recording")
    p.add_argument("--offline-voices", action="store_true", help="render tones instead of speech -- no TTS key, no cost")
    p.add_argument("--gap", type=float, default=0.4, help="seconds of silence between turns")
    p.add_argument("--jobs", type=int, default=4, help="synthesize N turns concurrently")
    p.add_argument("--offline", action="store_true", help="run every seat on the mock provider -- no keys, no cost, fake transcript")
    args = p.parse_args(argv)

    if not Path(args.panel).exists():
        print(f"panel file not found: {args.panel}", file=sys.stderr)
        return 2
    try:
        pnl = panel_mod.load(args.panel, offline=args.offline)
    except (ValueError, KeyError, ProviderError) as e:
        print(f"bad panel config: {e}", file=sys.stderr)
        return 2
    if args.policy:
        pnl.policy = args.policy

    print(f"# {pnl.show} — {args.topic}")
    print("  " + " · ".join(f"{s.name}={s.model}" for s in pnl.everyone))
    print(f"  policy={pnl.policy} turns={args.turns}" + ("  [OFFLINE MOCK]" if args.offline else ""))
    print()

    def show(t: orchestrator.Turn) -> None:
        tag = "*" if t.nudged else " "
        print(f"{tag}{t.speaker}: {t.text}\n")

    run = orchestrator.record(
        pnl,
        args.topic,
        turns=args.turns,
        host_every=args.host_every,
        dissent_after=args.dissent_after if args.dissent_after > 0 else 10**6,
        on_turn=show,
    )
    paths = transcript.save(run, args.out, slugify(args.topic))
    s = transcript.stats(run)
    print("---")
    print(f"{s['turns']} turns, {s['by_speaker']}, nudged={s['nudged_turns']}, tokens in/out={s['input_tokens']}/{s['output_tokens']}")
    for e in run.errors:
        print(f"! {e}", file=sys.stderr)
    print(f"transcript: {paths['markdown']}")
    print(f"run record: {paths['json']}")

    if args.audio:
        print()
        try:
            panel_mod.attach_voices(pnl, offline=args.offline_voices)
            result = render.render(
                run.to_dict(), pnl, paths["json"].with_suffix(""),
                gap_seconds=args.gap, jobs=args.jobs,
            )
        except (ValueError, VoiceError) as e:
            print(f"recorded fine, but rendering audio failed: {e}", file=sys.stderr)
            return 1
        for e in result["errors"]:
            print(f"! {e}", file=sys.stderr)
        print(f"episode:    {result['episode']} ({audio_mod.timestamp(result['seconds'])})")
        print(f"cues:       {result['cues']}")
    return 1 if run.errors and not run.turns else 0


if __name__ == "__main__":
    raise SystemExit(main())
