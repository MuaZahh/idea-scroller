"""Fetch YouTube transcripts for starter_story_ids.txt + superwall_ids.txt into JSON files."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

ROOT = Path(__file__).resolve().parent.parent


def load_ids(path: Path) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for line in path.read_text().splitlines():
        parts = line.split("|||")
        if len(parts) >= 2 and parts[0].strip():
            vid = parts[0].strip()
            title = parts[1].strip() if len(parts) > 1 else ""
            duration = parts[2].strip() if len(parts) > 2 else ""
            entries.append((vid, title, duration))
    return entries


def fetch_one(video_id: str) -> tuple[str, str | None, str | None]:
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        text = " ".join(snippet.text for snippet in fetched.snippets)
        return video_id, text, None
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as e:
        return video_id, None, f"{type(e).__name__}"
    except Exception as e:  # noqa: BLE001 - yt_dlp/api throw many undocumented errors
        return video_id, None, f"{type(e).__name__}: {str(e)[:100]}"


def run(ids_file: Path, output_json: Path, *, workers: int = 6) -> None:
    entries = load_ids(ids_file)
    existing: dict[str, dict] = {}
    if output_json.exists():
        raw = json.loads(output_json.read_text())
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, str):
                    existing[k] = {"title": "", "duration": "", "transcript": v}
                else:
                    existing[k] = v

    todo = [(vid, title, dur) for vid, title, dur in entries if vid not in existing or not existing[vid].get("transcript")]
    print(f"[{ids_file.name}] total={len(entries)} existing={len(existing)} to_fetch={len(todo)}")

    meta = {vid: (title, dur) for vid, title, dur in entries}
    results: dict[str, dict] = dict(existing)
    failures: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, vid): vid for vid, _, _ in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            vid = futures[fut]
            _, text, err = fut.result()
            title, dur = meta.get(vid, ("", ""))
            if text:
                results[vid] = {"title": title, "duration": dur, "transcript": text}
                print(f"  [{i}/{len(todo)}] ok {vid} ({len(text)} chars) — {title[:60]}")
            else:
                failures.append((vid, err or "unknown"))
                print(f"  [{i}/{len(todo)}] FAIL {vid} — {err}")

    for vid, entry in results.items():
        if not entry.get("title") and vid in meta:
            t, d = meta[vid]
            entry["title"] = t
            entry["duration"] = d

    output_json.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[{ids_file.name}] saved {len(results)} transcripts -> {output_json}")
    if failures:
        print(f"[{ids_file.name}] failures={len(failures)}")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "both"

    if target in ("starter", "both"):
        run(ROOT / "starter_story_ids.txt", ROOT / "starter_story_transcripts.json")
    if target in ("superwall", "both"):
        run(ROOT / "superwall_ids.txt", ROOT / "superwall_transcripts.json")
