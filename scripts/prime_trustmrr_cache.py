"""Prime the TrustMRR local corpus cache with unbuffered per-page progress."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from ideascroller.validators.trustmrr import (  # noqa: E402
    PAGINATION_LIMIT,
    _write_corpus,
    load_cached_corpus,
)


MAX_PAGES = 50  # top 2500 records by revenue — plenty for fuzzy match
CHECKPOINT_EVERY = 5  # write cache every N pages so we never lose progress


async def main() -> None:
    key = os.environ.get("TRUSTMRR_API_KEY")
    if not key:
        print("TRUSTMRR_API_KEY missing", flush=True)
        sys.exit(1)

    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    all_records: list[dict] = []
    page = 1

    async with httpx.AsyncClient(timeout=20, headers=headers) as client:
        while page <= MAX_PAGES:
            url = f"https://trustmrr.com/api/v1/startups?page={page}&limit={PAGINATION_LIMIT}&sort=revenue-desc"
            print(f"[page {page:>2}/{MAX_PAGES}] GET {url}", flush=True)
            try:
                resp = await client.get(url)
            except Exception as exc:
                print(f"[page {page:>2}] transport error: {exc!r}", flush=True)
                break
            if resp.status_code == 429:
                print(f"[page {page:>2}] 429 — backing off 30s", flush=True)
                await asyncio.sleep(30)
                continue
            if resp.status_code >= 400:
                print(f"[page {page:>2}] HTTP {resp.status_code} — stopping", flush=True)
                break
            body = resp.json()
            data = body.get("data") or []
            meta = body.get("meta") or {}
            all_records.extend(r for r in data if isinstance(r, dict))
            has_more = bool(meta.get("hasMore"))
            print(f"[page {page:>2}] +{len(data)}  total={len(all_records)}  hasMore={has_more}", flush=True)
            if page % CHECKPOINT_EVERY == 0 and all_records:
                _write_corpus(all_records)
                print(f"[page {page:>2}] checkpoint → cache ({len(all_records)} records)", flush=True)
            if not has_more:
                break
            page += 1
            await asyncio.sleep(3.2)

    if all_records:
        _write_corpus(all_records)
        print(f"\nwrote {len(all_records)} records to cache.", flush=True)
        cats: dict[str, int] = {}
        for r in all_records:
            cats.setdefault(r.get("category") or "?", 0)
            cats[r.get("category") or "?"] += 1
        print("\ncategory counts:")
        for c, n in sorted(cats.items(), key=lambda x: -x[1])[:20]:
            print(f"  {c}: {n}")
    else:
        print("no records — nothing written.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
