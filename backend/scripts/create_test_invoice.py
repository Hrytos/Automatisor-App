"""Create a finalized open Stripe invoice for N credits (does not auto-charge)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

from backend.main import PRICE_PER_CREDIT_USD_CENTS, get_admin_db  # noqa: E402


async def main(customer_id: str, credits: int) -> int:
    mode = "live" if os.environ["STRIPE_SECRET_KEY"].startswith("sk_live_") else "test"
    print(f"stripe_mode={mode}", flush=True)
    db = get_admin_db()
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "customer_id,email,stripe_customer_id",
            "customer_id": f"eq.{customer_id}",
            "limit": "1",
        },
    )
    if not rows:
        print("CUSTOMER_NOT_FOUND", flush=True)
        return 1
    customer = rows[0]
    stripe_customer_id = customer.get("stripe_customer_id")
    print(f"email={customer.get('email')}", flush=True)
    print(f"stripe_customer_id={stripe_customer_id}", flush=True)
    if not stripe_customer_id:
        print("ABORT: no stripe_customer_id", flush=True)
        return 1

    amount_cents = credits * PRICE_PER_CREDIT_USD_CENTS
    invoice = stripe.Invoice.create(
        customer=stripe_customer_id,
        auto_advance=False,
        collection_method="charge_automatically",
    )
    stripe.InvoiceItem.create(
        customer=stripe_customer_id,
        invoice=invoice.id,
        amount=amount_cents,
        currency="usd",
        description=f"[TEST] Automatisor — {credits} credit{'s' if credits != 1 else ''} (dev invoice)",
    )
    finalized = stripe.Invoice.finalize_invoice(invoice.id)
    print(f"invoice_id={finalized.id}", flush=True)
    print(f"number={finalized.number}", flush=True)
    print(f"status={finalized.status}", flush=True)
    print(f"amount_due_usd={(finalized.amount_due or 0) / 100}", flush=True)
    print(f"hosted={finalized.hosted_invoice_url}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    parser.add_argument("--credits", type=int, default=2)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.customer_id, args.credits)))
