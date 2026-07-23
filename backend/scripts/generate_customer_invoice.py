"""One-off: generate/charge Stripe invoice for a single customer via billing-cron logic."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

from backend.main import (  # noqa: E402
    PRICE_PER_CREDIT_USD_CENTS,
    _process_billing_period,
    get_admin_db,
)


async def main(customer_id: str) -> int:
    db = get_admin_db()
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "customer_id,email,stripe_customer_id,billing_period_start,billing_period_end,payment_method_id",
            "customer_id": f"eq.{customer_id}",
            "limit": "1",
        },
    )
    if not rows:
        print("CUSTOMER_NOT_FOUND", flush=True)
        return 1

    customer = rows[0]
    print(f"email={customer.get('email')}", flush=True)
    print(f"stripe_customer_id={customer.get('stripe_customer_id')}", flush=True)
    print(f"payment_method_id={customer.get('payment_method_id')}", flush=True)
    print(f"billing_period_start={customer.get('billing_period_start')}", flush=True)
    print(f"billing_period_end={customer.get('billing_period_end')}", flush=True)
    mode = "live" if os.environ["STRIPE_SECRET_KEY"].startswith("sk_live_") else "test"
    print(f"stripe_mode={mode}", flush=True)

    usage_params: dict = {
        "select": "billing_id,credits_used,usage_type,is_free,created_at",
        "customer_id": f"eq.{customer_id}",
    }
    if customer.get("billing_period_start"):
        usage_params["created_at"] = f"gte.{customer['billing_period_start']}"
    usage = await db.request("GET", "/rest/v1/automatisor_billing", params=usage_params) or []
    billable = [row for row in usage if row.get("is_free") is not True]
    total = sum(int(row.get("credits_used") or 0) for row in billable)
    amount_usd = total * PRICE_PER_CREDIT_USD_CENTS / 100
    print(f"billable_credits={total} amount_usd={amount_usd}", flush=True)
    for row in billable:
        print(
            f" usage created_at={row.get('created_at')} credits={row.get('credits_used')} "
            f"type={row.get('usage_type')} free={row.get('is_free')}",
            flush=True,
        )

    if not customer.get("stripe_customer_id"):
        print("ABORT: no stripe_customer_id", flush=True)
        return 1
    if not customer.get("payment_method_id"):
        print("ABORT: no payment_method_id", flush=True)
        return 1
    if total <= 0:
        print("ABORT: zero billable credits in current period", flush=True)
        return 1

    try:
        stripe.Customer.retrieve(customer["stripe_customer_id"])
    except Exception as exc:
        print(f"ABORT stripe retrieve: {type(exc).__name__}: {exc}", flush=True)
        return 1

    print("GENERATING invoice via _process_billing_period...", flush=True)
    now = datetime.now(timezone.utc)
    await _process_billing_period(db, customer, now)

    invoices = stripe.Invoice.list(customer=customer["stripe_customer_id"], limit=3)
    for inv in invoices.data:
        print(
            f"invoice id={inv.id} status={inv.status} amount_due={(inv.amount_due or 0) / 100} "
            f"number={inv.number} hosted={inv.hosted_invoice_url}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("customer_id")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.customer_id)))
