import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Ensure imports work when running this file directly.
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / "backend" / ".env")

from backend.main import create_stripe_customer, get_admin_db


async def fetch_customer_row(customer_id: str) -> dict[str, Any] | None:
    db = get_admin_db()
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "customer_id,email,full_name,stripe_customer_id,billing_period_start,billing_period_end",
            "customer_id": f"eq.{customer_id}",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def link_customer(customer_id: str) -> dict[str, Any]:
    db = get_admin_db()
    row = await fetch_customer_row(customer_id)
    if not row:
        return {"customer_id": customer_id, "status": "not_found"}

    stripe_customer_id = row.get("stripe_customer_id") or await create_stripe_customer(
        db,
        row["customer_id"],
        row["email"],
        row.get("full_name"),
    )

    refreshed = await fetch_customer_row(customer_id)
    return {
        "customer_id": customer_id,
        "status": "linked",
        "stripe_customer_id": (refreshed or {}).get("stripe_customer_id") or stripe_customer_id,
        "billing_period_start": (refreshed or {}).get("billing_period_start"),
        "billing_period_end": (refreshed or {}).get("billing_period_end"),
    }


async def run(customer_ids: list[str]) -> int:
    exit_code = 0
    for cid in customer_ids:
        result = await link_customer(cid)
        print(result)
        if result.get("status") != "linked":
            exit_code = 1
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or link Stripe customer IDs for Automatisor customers.")
    parser.add_argument(
        "--customer-id",
        action="append",
        dest="customer_ids",
        required=True,
        help="Customer ID to process. Provide this flag multiple times for multiple customers.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exit_code = asyncio.run(run(args.customer_ids))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()