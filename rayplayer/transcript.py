"""Turn a recorded run into something you can read, and something you can diff."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .orchestrator import Run


def stats(run: Run) -> dict:
    by_speaker = Counter(t.speaker for t in run.turns)
    return {
        "turns": len(run.turns),
        "by_speaker": dict(by_speaker),
        "nudged_turns": sum(1 for t in run.turns if t.nudged),
        "input_tokens": sum(t.input_tokens or 0 for t in run.turns),
        "output_tokens": sum(t.output_tokens or 0 for t in run.turns),
        "errors": len(run.errors),
    }


def to_markdown(run: Run) -> str:
    lines = [f"# {run.show}", "", f"**{run.topic}**", ""]
    roster = ", ".join(f"{p['name']} ({p['model']})" for p in run.panel)
    lines += [f"Recorded {run.started_at} · {roster}", "", "---", ""]
    for t in run.turns:
        prefix = "**" + t.speaker + "**"
        if t.nudged:
            prefix += " _(nudged: the room had been agreeing)_"
        lines += [f"{prefix} — {t.text}", ""]
    if run.errors:
        lines += ["---", "", "## Recording problems", ""]
        lines += [f"- {e}" for e in run.errors]
    return "\n".join(lines) + "\n"


def save(run: Run, out_dir: str | Path, slug: str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / f"{slug}.md", out / f"{slug}.json"
    md.write_text(to_markdown(run), encoding="utf-8")
    payload = run.to_dict()
    payload["stats"] = stats(run)
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": md, "json": js}
