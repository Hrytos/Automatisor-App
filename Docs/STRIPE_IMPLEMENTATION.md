# Stripe Integration — Detailed Implementation Document

**Model:** Postpaid (bill at end of period)  
**Free first period:** Yes — one credit, logged in `automatisor_billing`, no Stripe charge  
**Billing unit:** 1 credit = 1 pre-assessment run  

---

## 1. Supabase Schema Changes

Run these in the Supabase SQL editor **before any code changes**.

### 1a. Alter `automatisor_customer`

```sql
ALTER TABLE automatisor_customer
  ADD COLUMN stripe_customer_id   TEXT    UNIQUE,
  ADD COLUMN billing_period_start TIMESTAMPTZ,
  ADD COLUMN billing_period_end   TIMESTAMPTZ,
  ADD COLUMN payment_method_id    TEXT;
```

| Column | Type | Purpose |
|---|---|---|
| `stripe_customer_id` | `TEXT UNIQUE` | Stripe Customer ID (`cus_xxx`). NULL until onboarding completes. |
| `billing_period_start` | `TIMESTAMPTZ` | Start of the currently open billing window. |
| `billing_period_end` | `TIMESTAMPTZ` | End of the currently open billing window. Cron fires when this ≤ today. |
| `payment_method_id` | `TEXT` | The confirmed Stripe PaymentMethod ID (`pm_xxx`). NULL until user adds a card. |

### 1b. Alter `automatisor_billing`

```sql
ALTER TABLE automatisor_billing
  ADD COLUMN is_free BOOLEAN NOT NULL DEFAULT false;
```

| Column | Type | Purpose |
|---|---|---|
| `is_free` | `BOOLEAN DEFAULT false` | Set to `true` on all rows belonging to a customer's first period when the cron decides to skip charging. |

### First-period detection logic

The `is_free` column **drives** the cron decision. The rule:

```
SELECT COUNT(*) FROM automatisor_billing
WHERE customer_id = $1
  AND is_free = false
```

If this count is **0**, the customer has never been billed for real — this is their first period. Mark the current period's rows as `is_free = true`, skip Stripe, open next period. No extra column on `automatisor_customer` needed.

---

## 2. Python Dependencies (Poetry)

```bash
poetry add stripe
poetry add apscheduler
```

This adds to `pyproject.toml`:
- `stripe` — official Stripe Python SDK
- `apscheduler` — in-process cron scheduler inside FastAPI

---

## 3. Environment Variables

### `backend/.env` (server-only, never exposed to client)

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### `frontend/.env` (safe for client)

```
VITE_STRIPE_PUBLISHABLE_KEY=pk_test_...
```

Update `.env.example` to include all three keys (with placeholder values only).

---

## 4. Backend Changes — `backend/main.py`

### 4a. New imports and constants (top of file, after existing imports)

```python
import stripe
from apscheduler.schedulers.asyncio import AsyncIOScheduler

STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PRICE_PER_CREDIT_USD_CENTS = 100   # $1.00 per credit — adjust as needed

stripe.api_key = STRIPE_SECRET_KEY
```

---

### 4b. Update `find_customer_by_email` — add new columns to `select`

**Current** (line 374):
```python
"select": "customer_id,email,first_name,last_name,full_name,designation,company_name,company_domain,email_verified,email_verified_at,last_login_at,metadata",
```

**Change to:**
```python
"select": "customer_id,email,first_name,last_name,full_name,designation,company_name,company_domain,email_verified,email_verified_at,last_login_at,metadata,stripe_customer_id,billing_period_start,billing_period_end,payment_method_id",
```

This ensures `stripe_customer_id` and period fields are available everywhere `find_customer_by_email` is called (workspace state, invoices, usage, cron).

---

### 4c. New helper — `create_stripe_customer`

Add after `upsert_customer`:

```python
async def create_stripe_customer(db: SupabaseAdmin, customer_id: str, email: str, full_name: str | None) -> str:
    """
    Creates a Stripe Customer and writes stripe_customer_id + billing period
    back to automatisor_customer. Idempotent — if stripe_customer_id already
    exists, returns it without calling Stripe again.
    """
    existing = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "stripe_customer_id,billing_period_start",
            "customer_id": f"eq.{customer_id}",
            "limit": 1,
        },
    )
    row = existing[0] if existing else {}
    if row.get("stripe_customer_id"):
        return row["stripe_customer_id"]

    stripe_customer = stripe.Customer.create(
        email=email,
        name=full_name or email,
        metadata={"automatisor_customer_id": customer_id},
    )

    now = datetime.now(timezone.utc)
    # Billing period: now → first day of next month 00:00 UTC
    if now.month == 12:
        period_end = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        period_end = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer_id}"},
        json_body={
            "stripe_customer_id": stripe_customer.id,
            "billing_period_start": now.isoformat(),
            "billing_period_end": period_end.isoformat(),
        },
        headers={"Prefer": "return=minimal"},
    )
    return stripe_customer.id
```

---

### 4d. Hook `create_stripe_customer` into `handle_complete_onboarding`

**Where:** Inside the `if not dry_run:` block, **after** `await upsert_customer(...)` returns and `customer["customerId"]` is available.

**Current block (around line 1330):**
```python
customer = await upsert_customer(db, { ... })
await mark_customer_verified(db, email)
workspace = await build_workspace_payload(db, email, account["accountId"])
```

**Add one line after `upsert_customer`:**
```python
customer = await upsert_customer(db, { ... })
await mark_customer_verified(db, email)

# Create Stripe Customer if not already done
full_name = f"{first_name} {last_name}".strip()
await create_stripe_customer(db, customer["customerId"], email, full_name)

workspace = await build_workspace_payload(db, email, account["accountId"])
```

`create_stripe_customer` is idempotent — safe to call on re-onboarding.

---

### 4e. New endpoint — `POST /api/stripe/setup-intent`

Called when the user clicks "Add Payment Method" in the frontend.

```python
@app.post("/api/stripe/setup-intent")
async def create_setup_intent(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required.")
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="Stripe customer not initialised. Complete onboarding first.")

    intent = stripe.SetupIntent.create(
        customer=stripe_customer_id,
        usage="off_session",   # card will be charged server-side by cron
        payment_method_types=["card"],
    )
    return {"client_secret": intent.client_secret}
```

---

### 4f. New endpoint — `POST /api/stripe/confirm-payment-method`

Called by the frontend **after** Stripe.js confirms the SetupIntent client-side.

```python
@app.post("/api/stripe/confirm-payment-method")
async def confirm_payment_method(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    payment_method_id = (body.get("payment_method_id") or "").strip()
    if not email or not payment_method_id:
        raise HTTPException(status_code=422, detail="email and payment_method_id are required.")
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="Stripe customer not initialised.")

    # Set this card as the default for future off-session charges
    stripe.Customer.modify(
        stripe_customer_id,
        invoice_settings={"default_payment_method": payment_method_id},
    )

    # Persist the payment method ID locally for reference
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer['customer_id']}"},
        json_body={"payment_method_id": payment_method_id},
        headers={"Prefer": "return=minimal"},
    )
    return {"ok": True}
```

---

### 4g. Update `GET /api/billing/invoices` — replace Supabase query with Stripe

**Current (line 1760):** Queries `automatisor_invoices` table in Supabase.

**Replace the body** with:

```python
@app.post("/api/billing/invoices")
async def get_billing_invoices(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required.")
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        return {"invoices": []}  # No Stripe customer yet = no invoices

    stripe_invoices = stripe.Invoice.list(
        customer=stripe_customer_id,
        limit=12,
        expand=["data.payment_intent"],
    )

    invoices = []
    for inv in stripe_invoices.auto_paging_iter():
        invoices.append({
            "invoice_id":     inv.id,
            "invoice_number": inv.number,
            "invoice_date":   datetime.fromtimestamp(inv.created, tz=timezone.utc).isoformat(),
            "period_start":   datetime.fromtimestamp(inv.period_start, tz=timezone.utc).isoformat() if inv.period_start else None,
            "period_end":     datetime.fromtimestamp(inv.period_end, tz=timezone.utc).isoformat() if inv.period_end else None,
            "amount_usd":     inv.amount_paid / 100,
            "status":         inv.status,
            "pdf_url":        inv.invoice_pdf,
            "payment_url":    inv.hosted_invoice_url,
        })
        if len(invoices) >= 12:
            break

    return {"invoices": invoices}
```

**Why this shape exactly:** `BillingPage.jsx` reads `inv.invoice_id` (table key), `inv.invoice_number`, `inv.invoice_date`, `inv.period_start`, `inv.period_end`, `inv.amount_usd`, `inv.status`, `inv.pdf_url`, `inv.payment_url`. This matches exactly.

---

### 4h. New endpoint — `POST /api/stripe/webhook`

**Critical:** This endpoint must read the **raw request body bytes** before Stripe signature verification. It must NOT use `Body(default={})`.

```python
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    payload = await request.body()          # raw bytes — required for sig verification
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event_type = event["type"]
    data_object = event["data"]["object"]

    if event_type == "invoice.payment_succeeded":
        # The cron creates invoices and calls invoice.pay() — this confirms it worked.
        # Nothing else needed; the invoice is already in Stripe and fetchable via
        # /api/billing/invoices. Log for debugging if desired.
        pass

    elif event_type == "invoice.payment_failed":
        # Optional: surface a warning in the customer's workspace state.
        # For now, Stripe will retry automatically per your retry settings.
        pass

    elif event_type == "setup_intent.succeeded":
        # SetupIntent confirmed client-side — payment method is ready.
        # The /api/stripe/confirm-payment-method endpoint handles the DB write;
        # this webhook is a backup confirmation.
        pass

    return {"received": True}
```

**Stripe Dashboard configuration:** Register the webhook endpoint URL as `https://yourdomain.com/api/stripe/webhook`. Subscribe to:
- `invoice.payment_succeeded`
- `invoice.payment_failed`
- `setup_intent.succeeded`

---

### 4i. Cron job — monthly billing

Add after all endpoint definitions, before `register_vercel_service_api_aliases()`.

```python
# ── Billing cron ─────────────────────────────────────────────

async def run_billing_cron() -> None:
    """
    Runs daily at 01:00 UTC.
    Finds all customers whose billing_period_end <= now and processes them.
    """
    db = get_admin_db()
    now = datetime.now(timezone.utc)

    due_customers = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "customer_id,email,stripe_customer_id,billing_period_start,billing_period_end,payment_method_id",
            "billing_period_end": f"lte.{now.isoformat()}",
            "stripe_customer_id": "not.is.null",
        },
    )

    for customer in due_customers or []:
        try:
            await _process_billing_period(db, customer, now)
        except Exception as exc:
            # Log and continue — one failure must not block other customers
            print(f"[billing-cron] ERROR for {customer.get('email')}: {exc}")


async def _process_billing_period(db: SupabaseAdmin, customer: dict[str, Any], now: datetime) -> None:
    customer_id       = customer["customer_id"]
    stripe_customer_id = customer["stripe_customer_id"]
    period_start      = customer.get("billing_period_start")
    period_end        = customer.get("billing_period_end")

    # ── Determine if this is the first real period ────────────
    prior_paid_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_billing",
        params={
            "select": "billing_id",
            "customer_id": f"eq.{customer_id}",
            "is_free": "eq.false",
            "limit": 1,
        },
    )
    is_first_period = not bool(prior_paid_rows)

    # ── Fetch usage rows for this period ─────────────────────
    usage_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_billing",
        params={
            "select": "billing_id,credits_used",
            "customer_id": f"eq.{customer_id}",
            "created_at": f"gte.{period_start}" if period_start else None,
            # Filter rows within this period only if period_start is set
        },
    )
    usage_rows = [r for r in (usage_rows or []) if r.get("billing_id")]
    total_credits = sum(int(r.get("credits_used") or 0) for r in usage_rows)
    row_ids = [r["billing_id"] for r in usage_rows]

    # ── Open next billing period ──────────────────────────────
    next_period_start = now
    if now.month == 12:
        next_period_end = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        next_period_end = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer_id}"},
        json_body={
            "billing_period_start": next_period_start.isoformat(),
            "billing_period_end": next_period_end.isoformat(),
        },
        headers={"Prefer": "return=minimal"},
    )

    if is_first_period:
        # ── Free period — mark rows, skip Stripe ─────────────
        if row_ids:
            await db.request(
                "PATCH",
                "/rest/v1/automatisor_billing",
                params={"billing_id": f"in.({','.join(row_ids)})"},
                json_body={"is_free": True},
                headers={"Prefer": "return=minimal"},
            )
        print(f"[billing-cron] FREE period closed for {customer.get('email')} ({total_credits} credits)")
        return

    # ── Paid period — charge via Stripe ──────────────────────
    if total_credits == 0:
        print(f"[billing-cron] Zero usage for {customer.get('email')} — no invoice created")
        return

    if not customer.get("payment_method_id"):
        print(f"[billing-cron] WARNING: {customer.get('email')} has no payment method — skipping charge")
        return

    amount_cents = total_credits * PRICE_PER_CREDIT_USD_CENTS
    period_label = ""
    if period_start and period_end:
        try:
            ps = datetime.fromisoformat(str(period_start).replace("Z", "+00:00"))
            pe = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
            period_label = f"{ps.strftime('%b %d')} – {pe.strftime('%b %d, %Y')}"
        except ValueError:
            pass

    stripe.InvoiceItem.create(
        customer=stripe_customer_id,
        amount=amount_cents,
        currency="usd",
        description=f"Automatisor — {total_credits} credit{'s' if total_credits != 1 else ''} ({period_label})",
    )
    invoice = stripe.Invoice.create(
        customer=stripe_customer_id,
        auto_advance=True,
        collection_method="charge_automatically",
        default_payment_method=customer["payment_method_id"],
    )
    stripe.Invoice.pay(invoice.id)
    print(f"[billing-cron] Charged {customer.get('email')}: ${amount_cents / 100:.2f} ({total_credits} credits)")


# ── APScheduler setup ─────────────────────────────────────────

_scheduler = AsyncIOScheduler(timezone="UTC")


@app.on_event("startup")
async def start_scheduler() -> None:
    _scheduler.add_job(run_billing_cron, "cron", hour=1, minute=0)
    _scheduler.start()


@app.on_event("shutdown")
async def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
```

---

## 5. Frontend Changes — `frontend/src/`

### 5a. `BillingPage.jsx` — no data-layer changes needed

The `/api/billing/invoices` endpoint response shape is unchanged. The component already reads `invoice_id`, `invoice_number`, `invoice_date`, `period_start`, `period_end`, `amount_usd`, `status`, `pdf_url`, `payment_url` — which is exactly what the new Stripe-backed endpoint returns.

**Only addition needed:** A "Add Payment Method" section above the invoices table.

### 5b. Card setup flow in `BillingPage.jsx`

Install frontend Stripe packages:

```bash
cd frontend
npm install @stripe/stripe-js @stripe/react-stripe-js
```

Flow:
1. User clicks **"Add Payment Method"** button.
2. Frontend calls `POST /api/stripe/setup-intent` → receives `{ client_secret }`.
3. Mount Stripe's `CardElement` in an inline form.
4. On submit: call `stripe.confirmCardSetup(client_secret, { payment_method: { card: cardElement } })`.
5. On success: call `POST /api/stripe/confirm-payment-method` with `{ email, payment_method_id }`.
6. Show success confirmation, hide the card form.

**Environment variable access in Vite:**
```jsx
const stripe = await loadStripe(import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY);
```

### 5c. `CreditsPage.jsx` — add free period badge

The `GET /api/credits/usage` response already includes `billing_periods`. After the schema + cron changes, rows with `is_free = true` will exist in the DB.

Add a small UI indicator on the period card when all rows in a period are free:
- Read `is_free` from each row in the period's `rows[]` array
- If every row has `is_free: true` (or the period has 0 rows but is_free context), show a "Free period" badge next to the period heading

> **Note:** The backend `/api/credits/usage` endpoint needs to include `is_free` in its `automatisor_billing` select list for the frontend to read it.

---

## 6. Backend: Update `get_credits_usage` select list

**In the existing `GET /api/credits/usage` endpoint (line 1641), update the Supabase query:**

**Current:**
```python
"select": "site_id,usage_type,credits_used,created_at",
```

**Change to:**
```python
"select": "site_id,usage_type,credits_used,created_at,is_free",
```

And include `is_free` in each row object being built in the response:
```python
periods[key].append({
    ...
    "is_free": bool(row.get("is_free")),
})
```

---

## 7. Security Checklist

| Requirement | Implementation |
|---|---|
| `STRIPE_SECRET_KEY` never sent to client | Set only in `backend/.env`, read via `os.getenv` in `main.py` |
| `STRIPE_WEBHOOK_SECRET` never sent to client | Same as above |
| Raw body for webhook signature verification | `await request.body()` — **not** `Body(default={})` |
| `stripe.Webhook.construct_event` always called | Before any event processing — rejects tampered payloads |
| Publishable key only on frontend | `VITE_STRIPE_PUBLISHABLE_KEY` in `frontend/.env` |
| Card data never touches our server | Stripe.js tokenises client-side → we only receive `pm_xxx` ID |
| Webhook route excluded from CORS | Only Stripe's servers call it; no browser CORS needed |

---

## 8. Build Order

```
Step 1  →  Run SQL migrations in Supabase (Schema section 1a + 1b)
Step 2  →  poetry add stripe apscheduler
Step 3  →  Add STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET to .env
Step 4  →  4a — New imports + constants in main.py
Step 5  →  4b — Update find_customer_by_email select list
Step 6  →  4c — Add create_stripe_customer helper
Step 7  →  4d — Hook create_stripe_customer into handle_complete_onboarding
Step 8  →  4e — Add POST /api/stripe/setup-intent
Step 9  →  4f — Add POST /api/stripe/confirm-payment-method
Step 10 →  4g — Rewrite POST /api/billing/invoices to use Stripe
Step 11 →  6   — Update get_credits_usage select to include is_free
Step 12 →  4h — Add POST /api/stripe/webhook
Step 13 →  4i — Add cron job + APScheduler startup/shutdown
Step 14 →  5b — Add card setup UI in BillingPage.jsx
Step 15 →  5c — Add free period badge in CreditsPage.jsx
Step 16 →  Register webhook URL in Stripe Dashboard
```

---

## 9. What is NOT changing

- `automatisor_billing` insert logic inside `insert_billing_usage` — unchanged, just gains `is_free` column with `DEFAULT false`
- `/api/credits/usage` response shape — unchanged, just adds `is_free` field per row
- `BillingPage.jsx` invoice table rendering — unchanged, field names already match
- Auth flow (`handle_request_otp`, `handle_verify_otp`) — unchanged
- All address validation endpoints — unchanged
