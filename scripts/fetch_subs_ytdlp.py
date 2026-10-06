"""Fetch YouTube subtitles via yt-dlp (fallback when transcript API is IP-blocked)."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_ids(path: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for line in path.read_text().splitlines():
        parts = line.split("|||")
        if len(parts) >= 2 and parts[0].strip():
            out.append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else "", parts[2].strip() if len(parts) > 2 else ""))
    return out


VTT_TAG_RE = re.compile(r"<[^>]+>")
VTT_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}\.\d{3}")


def vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:") or VTT_TIME_RE.match(line):
            continue
        if line.startswith("NOTE") or line.isdigit():
            continue
        line = VTT_TAG_RE.sub("", line).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return " ".join(lines)


def fetch_one(video_id: str) -> tuple[str, str | None, str | None]:
    with tempfile.TemporaryDirectory() as td:
        tmpl = str(Path(td) / "%(id)s.%(ext)s")
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--skip-download",
            "--write-auto-subs", "--write-subs",
            "--sub-lang", "en,en-US,en-GB,en-orig",
            "--sub-format", "vtt",
            "-o", tmpl,
            f"https://www.youtube.com/watch?v={video_id}",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return video_id, None, "timeout"
        vtts = list(Path(td).glob("*.vtt"))
        if not vtts:
            tail = (proc.stderr or "")[-200:].strip()
            return video_id, None, f"no_vtt ({tail})"
        vtt = vtts[0].read_text(encoding="utf-8", errors="ignore")
        text = vtt_to_text(vtt)
        if not text:
            return video_id, None, "empty_vtt"
        return video_id, text, None


def run(ids_file: Path, output_json: Path, *, workers: int = 4) -> None:
    entries = load_ids(ids_file)
    results: dict[str, dict] = {}
    if output_json.exists():
        raw = json.loads(output_json.read_text())
        for k, v in raw.items():
            if isinstance(v, str):
                results[k] = {"title": "", "duration": "", "transcript": v}
            else:
                results[k] = v
    meta = {vid: (title, dur) for vid, title, dur in entries}
    todo = [vid for vid, _, _ in entries if vid not in results or not results[vid].get("transcript")]
    print(f"[{ids_file.name}] total={len(entries)} have={len(results)} to_fetch={len(todo)}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, vid): vid for vid in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            vid = futures[fut]
            _, text, err = fut.result()
            title, dur = meta.get(vid, ("", ""))
            if text:
                results[vid] = {"title": title, "duration": dur, "transcript": text}
                print(f"  [{i}/{len(todo)}] ok {vid} ({len(text)} chars) — {title[:60]}")
            else:
                print(f"  [{i}/{len(todo)}] FAIL {vid} — {err}")

    for vid, entry in results.items():
        if not entry.get("title") and vid in meta:
            t, d = meta[vid]
            entry["title"] = t
            entry["duration"] = d

    output_json.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[{ids_file.name}] saved {len(results)} -> {output_json}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "superwall"
    if target in ("starter", "both"):
        run(ROOT / "starter_story_ids.txt", ROOT / "starter_story_transcripts.json")
    if target in ("superwall", "both"):
        run(ROOT / "superwall_ids.txt", ROOT / "superwall_transcripts.json")
