"""
run_sample_backfill.py

One-time script: provision the BR Williams sample site row in
automatisor_customer_sites for every existing customer who doesn't
already have one.

Run AFTER:
  1. Applying Docs/automatisor_sample_site_migration.sql in Supabase
  2. Deploying the backend code

Usage:
    poetry run python backend/run_sample_backfill.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

from backend.main import (  # noqa: E402
    SAMPLE_SITE_ID,
    SupabaseAdmin,
    ensure_sample_site_row,
)


async def run():
    db = SupabaseAdmin()

    # Fetch all customer IDs
    page_size = 1000
    offset = 0
    all_customer_ids: list[str] = []
    while True:
        rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer",
            params={
                "select": "customer_id",
                "order": "created_at.asc",
                "limit": str(page_size),
                "offset": str(offset),
            },
        )
        if not rows:
            break
        all_customer_ids.extend(r["customer_id"] for r in rows if r.get("customer_id"))
        if len(rows) < page_size:
            break
        offset += page_size

    print(f"Found {len(all_customer_ids)} customers. Provisioning sample rows...")

    created = 0
    skipped = 0
    failed = 0

    for customer_id in all_customer_ids:
        try:
            existing = await db.request(
                "GET",
                "/rest/v1/automatisor_customer_sites",
                params={
                    "select": "customer_site_id",
                    "customer_id": f"eq.{customer_id}",
                    "site_id": f"eq.{SAMPLE_SITE_ID}",
                    "assigned_via": "eq.sample_site",
                    "limit": "1",
                },
            )
            if existing:
                skipped += 1
                continue
            await ensure_sample_site_row(db, customer_id)
            created += 1
            print(f"  created  {customer_id}")
        except Exception as exc:
            failed += 1
            print(f"  FAILED   {customer_id}: {exc}")

    print(f"\nDone. created={created}  skipped={skipped}  failed={failed}")


if __name__ == "__main__":
    asyncio.run(run())
