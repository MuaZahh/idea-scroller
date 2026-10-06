"""Background worker: retry Superwall transcripts with long backoff until rate limit clears."""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.fetch_transcripts import fetch_one, load_ids

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ids_file = ROOT / "superwall_ids.txt"
    out_file = ROOT / "superwall_transcripts.json"
    entries = load_ids(ids_file)
    meta = {vid: (t, d) for vid, t, d in entries}

    results: dict[str, dict] = {}
    if out_file.exists():
        raw = json.loads(out_file.read_text())
        for k, v in raw.items():
            results[k] = v if isinstance(v, dict) else {"title": "", "duration": "", "transcript": v}

    pending = [vid for vid, _, _ in entries if vid not in results or not results[vid].get("transcript")]
    print(f"[retry] starting with {len(pending)} pending")

    attempt = 0
    base_wait = 60
    while pending:
        attempt += 1
        wait = base_wait + random.randint(0, 30)
        print(f"[retry] round {attempt}: {len(pending)} left, waiting {wait}s")
        time.sleep(wait)
        got_any = False
        for vid in list(pending):
            _, text, err = fetch_one(vid)
            title, dur = meta.get(vid, ("", ""))
            if text:
                results[vid] = {"title": title, "duration": dur, "transcript": text}
                pending.remove(vid)
                got_any = True
                print(f"[retry] ok {vid} ({len(text)} chars) — remaining {len(pending)}")
                out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
                time.sleep(3)
            else:
                # Short label only
                tag = "blocked" if "IpBlocked" in (err or "") or "blocking" in (err or "") else (err or "?")
                print(f"[retry] fail {vid} ({tag[:40]})")
                time.sleep(5)
                if "blocked" in tag.lower() or "IpBlocked" in tag:
                    # Abort this round — IP is throttled
                    break
        if not got_any:
            base_wait = min(base_wait * 2, 900)
        else:
            base_wait = 60
        if attempt > 25:
            print("[retry] giving up after 25 rounds")
            break

    print(f"[retry] done. final total: {sum(1 for v in results.values() if v.get('transcript'))}")


if __name__ == "__main__":
    main()
