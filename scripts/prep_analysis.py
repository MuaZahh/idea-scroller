"""Split transcripts into analysis chunks for parallel LLM analysis."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "validation_analysis"
OUT_DIR.mkdir(exist_ok=True)


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            out[k] = {"title": "", "duration": "", "transcript": v}
        else:
            out[k] = v
    return {k: v for k, v in out.items() if v.get("transcript")}


def chunk_text(all_entries: list[tuple[str, str, str, str]], source: str, chunks: int = 4) -> None:
    per = max(1, (len(all_entries) + chunks - 1) // chunks)
    for i in range(chunks):
        batch = all_entries[i * per : (i + 1) * per]
        if not batch:
            continue
        lines: list[str] = [f"# {source} — batch {i+1} ({len(batch)} videos)\n"]
        for vid, title, duration, transcript in batch:
            lines.append(f"\n===== VIDEO {vid} =====")
            lines.append(f"TITLE: {title}")
            lines.append(f"DURATION: {duration}")
            lines.append("TRANSCRIPT:")
            lines.append(transcript.strip())
            lines.append("")
        out = OUT_DIR / f"{source}_batch_{i+1}.txt"
        out.write_text("\n".join(lines))
        print(f"  wrote {out.name}: {len(batch)} videos, {sum(len(t) for *_, t in batch)} chars")


def main() -> None:
    starter = load(ROOT / "starter_story_transcripts.json")
    superwall = load(ROOT / "superwall_transcripts.json")

    starter_entries = [(k, v.get("title", ""), str(v.get("duration", "")), v["transcript"]) for k, v in starter.items()]
    super_entries = [(k, v.get("title", ""), str(v.get("duration", "")), v["transcript"]) for k, v in superwall.items()]

    print(f"Starter Story: {len(starter_entries)} transcripts")
    print(f"Superwall: {len(super_entries)} transcripts")

    chunk_text(starter_entries, "starter", chunks=6)
    if super_entries:
        chunk_text(super_entries, "superwall", chunks=1)


if __name__ == "__main__":
    main()
