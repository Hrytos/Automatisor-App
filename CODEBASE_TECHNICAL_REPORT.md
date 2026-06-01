# AutomatiSOR — Codebase Technical Report

> Generated: May 2026  
> Scope: Full codebase analysis — frontend and backend  
> Purpose: Foundation for frontend and backend rulebooks

---

## 1. Project Overview

### What This Webapp Does

AutomatiSOR is a B2B SaaS warehouse automation advisory platform. It allows industrial teams to:

1. Create a verified account using a **work email + OTP** (personal inboxes blocked)
2. **Onboard** their company profile and first warehouse facility site
3. Add and manage multiple **warehouse facility sites** tied to their company account
4. Request a **site pre-assessment report** for any saved site (costs 1 credit per request)
5. View the **structured pre-assessment report** (delivered asynchronously, notified by email)
6. Leave **notes** on a site and **rate** the generated report
7. Track **credit usage** by billing period
8. View **invoices** and payments

A sample report is pinned in every workspace dashboard, showing the report format using a real public dataset (BR Williams, Anniston, Alabama).

### Main User Flows

1. **Auth flow** — `/auth` → email check → OTP request → OTP verify → new user: onboarding stage, existing user: workspace
2. **Onboarding flow** — fill profile + company details + first site (with Google Maps picker and address validation)
3. **Add site flow** — `/workspace/sites/new` → enter company + domain + pick address → validate → continue to pre-assessment
4. **Pre-assessment flow** — `/workspace/pre-assessment` → review site, accept credit cost, confirm → job queued → email confirmation sent
5. **Report view flow** — `/workspace/report` → tabs: Pre-assessment | Notes, with inline rating
6. **Credits/Billing flows** — `/workspace/credits`, `/workspace/billing`

### Main Modules/Features

| Module | Description |
|---|---|
| Auth & OTP | Work-email-only signup, Supabase OTP |
| Onboarding | Profile + company + first site setup |
| Site management | Add, list, and view facility sites |
| Address validation | Google Places API company-at-address check with candidate suggestions |
| Pre-assessment request | Credit-gated job trigger with review modal |
| Structured report viewer | Multi-section, confidence-filtered, collapsible report |
| Notes & Rating | Per-site notes textarea + 5-star rating on 3 dimensions |
| Credits & Billing | 30-day rolling credit usage tracking + invoice list |

---

## 2. Tech Stack

### Frontend

| Concern | Technology |
|---|---|
| Framework | React 18 |
| Language | JavaScript (JSX) — no TypeScript |
| Build tool | Vite 5 |
| Routing | React Router DOM v6 |
| Styling system | Vanilla CSS — single file at `frontend/public/css/styles.css` |
| UI/component library | None — all components hand-written |
| State management | React `useState` + `useEffect` only — no Redux, Zustand, Jotai, or Context |
| Form handling | Uncontrolled inputs with manual `useState` — no React Hook Form or Formik |
| API calling pattern | Native `fetch` wrapped in a local `fetchJson()` utility — no Axios, React Query, or SWR |
| Auth method | `window.sessionStorage` for session state + httpOnly `access_token` cookie set by backend |
| Hosting/deployment | Vercel (confirmed via `vercel.json` with `experimentalServices` config) |

### Backend

| Concern | Technology |
|---|---|
| Framework | FastAPI 0.115 |
| Language | Python 3.11+ |
| Database | Supabase (PostgreSQL) |
| ORM/query layer | None — raw HTTP calls to Supabase REST API via a custom `SupabaseAdmin` class + `httpx` |
| Auth/session handling | Supabase OTP auth (via `supabase-py`), httpOnly `access_token` cookie — but cookie is **not validated** in most route handlers |
| File storage | None currently |
| AI/agent layer | None currently — pre-assessment jobs appear to be triggered manually/externally (no job queue in this codebase) |
| Email | Resend API — transactional emails for OTP (via Supabase) and pre-assessment confirmation |
| Notifications | Slack incoming webhook for new pre-assessment requests |
| Background jobs/queues | None in codebase — report generation is external (the backend only marks `is_report_ready` and notifies) |
| Deployment | Vercel Fluid Functions / `python -m uvicorn` — confirmed via `vercel.json` and root `package.json` start scripts |

---

## 3. Current Folder Structure

```
Automatisor-App/
├── package.json               # Root — dev scripts for running backend + frontend
├── pyproject.toml             # Poetry config for Python deps
├── vercel.json                # Vercel deployment config (frontend + backend split)
├── text.md                    # Unknown — appears to be a scratch/notes file
│
├── backend/
│   ├── __init__.py
│   ├── main.py                # ⚠️ ENTIRE backend — routes, services, DB access, utils, email, ~1900 lines
│   ├── address_normalization.py  # ✅ Clean utility — state/street/zip normalization
│   ├── address_validator.py      # ✅ Clean utility — Google Places company validation
│   ├── requirements.txt          # Pip-style lockfile (redundant — Poetry is the source of truth)
│   ├── test_address_validator.py # Tests for address_validator.py
│   └── sample-report/
│       └── data_structure.json   # Sample pre-assessment report data (BR Williams)
│
└── frontend/
    ├── index.html             # SPA entry point
    ├── package.json           # Frontend dependencies
    ├── vite.config.js         # Vite config — dev proxy /api → localhost:3000
    └── src/
        ├── main.jsx           # React root mount
        ├── App.jsx            # ⚠️ ENTIRE frontend — all pages, components, hooks, utils, ~4600+ lines
        ├── BillingPage.jsx    # Billing page — standalone but duplicates session utilities
        ├── BillingPageDemo.jsx # Demo version with hardcoded data — no session required
        ├── CreditsPage.jsx    # Credits page — standalone but duplicates session utilities
        ├── CreditsPageDemo.jsx # Demo version with hardcoded data — no session required
        └── report_section_structure.json  # Report section/table schema config
```

### Folder Assessment

| Folder/File | What It Stores | Organization Assessment |
|---|---|---|
| `backend/` | All Python code | **Messy** — single 1900-line monolith in `main.py` with no sub-folders |
| `backend/sample-report/` | Sample JSON report data | Clean but should be in `frontend/src/data/` — frontend imports it directly |
| `frontend/src/` | All React source | **Very messy** — 4600-line monolith in `App.jsx` with no component/hooks/utils folders |
| `frontend/public/css/` | One CSS file | Manageable now but will become unmaintainable as UI grows |
| `frontend/public/images/` | Static images | Clean |

---

## 4. Frontend Architecture Analysis

### Page/Routing Structure

All routes are defined at the bottom of `App.jsx` inside the `App` component:

| Route | Component | Notes |
|---|---|---|
| `/` | `Navigate → /auth` | Redirect |
| `/auth` | `NewUserPage` | Handles email, OTP, and onboarding stages in one component via `stage` state |
| `/new-user` | `Navigate → /auth` | Legacy redirect |
| `/workspace` | `WorkspacePage` | Dashboard — list of saved sites |
| `/workspace/sites/new` | `NewSitePage` | Add facility form |
| `/workspace/pre-assessment` | `PreAssessmentPage` | Pre-assessment flow |
| `/workspace/report` | `ReportPage` | Report viewer (tabs: Pre-assessment, Notes) |
| `/workspace/credits` | `CreditsPage` | Separate file |
| `/workspace/billing` | `BillingPage` | Separate file |
| `/demo/credits` | `CreditsPageDemo` | Separate file |
| `/demo/billing` | `BillingPageDemo` | Separate file |
| `/sample-reports/br-williams` | `SampleReportPage` | Sample report viewer |
| `*` | `Navigate → /auth` | Catch-all redirect |

**Problem:** `/auth` handles three distinct UI stages (`email`, `otp`, `onboarding`) via local `stage` state in a single component. These should be separate route steps or at minimum separate sub-components.

### Component Organization

There is **no component organization**. Every UI piece lives in `App.jsx`:

- `AppNav` — top navigation bar
- `HomePage` — marketing landing page (currently unreachable — `/` redirects to `/auth`)
- `NewUserPage` — auth + onboarding page (stage-driven: email → OTP → onboarding)
- `WorkspacePage` — dashboard
- `NewSitePage` — add facility
- `PreAssessmentPage` — request flow
- `ReportPage` — report viewer
- `SampleReportPage` — static sample report
- `SiteRow` — site list item
- `PinnedSampleReportRow` — pinned demo site in list
- `GoogleAddressPicker` — full Google Maps + autocomplete integration (200+ lines, `forwardRef`)
- `AddressValidationPanel` — address validation UI with candidate listing
- `CandidateConfirmationModal` — modal for confirming a candidate address
- `StructuredPreAssessmentReport` — full report renderer
- `StructuredReportSection` — section accordion
- `StructuredReportItem` — item accordion
- `StructuredReportKeyValueTable` — key-value table
- `StructuredReportRecordsTable` — records table
- `StructuredOperationalSnapshotTable` — operational snapshot table
- `ReportConfidenceBadge` — confidence badge chip
- `ReportRatingPanel` — rating UI for report feedback
- `CreditsUsedChip` — credits chip in workspace nav
- `WorkspaceMobileActions` — mobile bottom nav

`BillingPage.jsx`, `CreditsPage.jsx`, `BillingPageDemo.jsx`, `CreditsPageDemo.jsx` are correctly separate files but share no code with each other or `App.jsx`.

### Where Business Logic Currently Lives

All business logic lives inside `App.jsx`:

- **Report parsing logic** — `flattenStructuredReportRows`, `structuredReportRowsFromConfig`, `structuredReportRecordsFromConfig`, `structuredOperationalSnapshotRowsFromConfig`, `filterStructuredReportItem`, `collectStructuredConfidenceCounts` — all pure functions dumped as module-level functions inside `App.jsx`
- **Confidence scoring** — `confidenceBand`, `reportConfidenceLabel` — inline utility functions
- **Address parsing** — `structuredAddressFromComponents`, `cityFromFormattedAddress`, `normalizeResolvedAddress`, `resolvedAddressFromCandidate` — inline
- **Google Maps loading** — `loadGoogleMaps`, `waitForGoogleFeature` — module-level async functions in `App.jsx`
- **Session management** — `loadSession`, `saveSession`, `clearSession`, `buildSessionFromPayload` — module-level functions
- **OTP feedback normalization** — `normalizeAuthFeedback` — inline
- **Pre-assessment route state construction** — `buildPendingSiteFromInput`, `buildPreAssessmentRouteState` — inline

### How API Calls Are Made

All API calls go through a local `fetchJson()` function defined at the top of `App.jsx`. It:
- Uses native `fetch` with `credentials: "same-origin"`
- Sets `Content-Type: application/json`
- Parses JSON response
- Throws an `Error` with `payload.detail` if response is not ok
- Attaches `error.code` and `error.payload` to the thrown error

**Problem:** `fetchJson` is copy-pasted identically into `App.jsx`, `BillingPage.jsx`, and `CreditsPage.jsx`. There is no shared API client module.

API calls are made directly inside:
- `useEffect` hooks inside page components
- Event handlers (`onClick`, `onSubmit`)
- Async functions called by event handlers

There is no API abstraction layer, no request/response interceptors, and no centralized error handling.

### How Auth State Is Handled

Auth state is stored in `window.sessionStorage` under the key `"automatisor_auth_workspace_v2"`.

The session object contains:
- `email`, `userMode`, `nextStep`, `authVerified`
- `customerId`, `accountId`, `activeAccountId`
- `companyName`, `companyDomain`
- `creditsUsedTotal`, `creditsUsedThisMonth`
- `sites`, `accounts`
- `preAssessmentPriceCredits`

The `useRequireSession` hook reads from sessionStorage on mount and redirects to `/auth` if no session exists.

**Problems:**
1. `useRequireSession` is copy-pasted into `App.jsx`, `BillingPage.jsx`, and `CreditsPage.jsx` — three identical implementations
2. `SESSION_KEY`, `loadSession`, `fetchJson` are also copy-pasted across all three files
3. Session state drives all "auth" — there is no server-side session check, token validation, or refresh on page load for protected routes
4. `sessionStorage` clears on tab close — users must re-auth every session, which is intentional but not documented

### How Forms Are Handled

All forms use uncontrolled `useState` — no form library. Pattern:
```jsx
const [form, setForm] = useState({ field: "" });
<input value={form.field} onChange={(e) => setForm(c => ({ ...c, field: e.target.value }))} />
```

Validation is done inline in submit handlers. There is no schema-level validation (no Zod, Yup). Error state is a single `string` per page, displayed in a `<p className="form-error">` element.

### How Errors/Loading States Are Handled

**Error handling:**
- Each page uses 1–3 `useState` string variables: `error`, `message`, `reviewError`, `notesMessage`, `ratingMessage`
- Errors are shown via `<p className="form-error ${error ? '' : 'hidden'}">{error}</p>`
- No toast/snackbar system, no centralized error boundary

**Loading states:**
- Each page uses `loading` state as either a `boolean` or a `string` discriminator (`"email"`, `"otp"`, `"onboarding"`, etc.)
- Buttons are disabled during loading with `"Please wait..."` label
- No skeleton loaders or loading indicators beyond inline text

### Whether Components Are Too Large or Mixed With Logic

Yes — severely.

- `App.jsx` is 4600+ lines. It contains every page, every component, every utility function, and all business logic
- `NewUserPage` alone manages email, OTP, and onboarding as three conditional render stages with 20+ state variables
- `GoogleAddressPicker` is a `forwardRef` component 300+ lines long, handling Google Maps initialization, legacy/new autocomplete fallback, geocoding, marker dragging, and address resolution
- `StructuredPreAssessmentReport` manages section navigation, confidence filter, accordion open/close state, and report rendering in one component
- `PreAssessmentPage` handles workspace loading, pending site logic, existing site lookup, pre-assessment submission, and review modal in one component

### Duplicate Patterns and Inconsistent Naming

**Duplicated code across files:**

| Item | Duplicated In |
|---|---|
| `SESSION_KEY` constant | `App.jsx`, `BillingPage.jsx`, `CreditsPage.jsx` |
| `loadSession()` function | `App.jsx`, `BillingPage.jsx`, `CreditsPage.jsx` |
| `fetchJson()` function | `App.jsx`, `BillingPage.jsx`, `CreditsPage.jsx` |
| `useRequireSession()` hook | `App.jsx`, `BillingPage.jsx`, `CreditsPage.jsx` |
| `formatInvoiceDate()` | `BillingPage.jsx`, `BillingPageDemo.jsx` |
| `formatCurrency()` | `BillingPage.jsx`, `BillingPageDemo.jsx` |
| `formatDateTimeEST()` | `CreditsPage.jsx`, `CreditsPageDemo.jsx` |
| `formatPeriodDate()` | `CreditsPage.jsx`, `CreditsPageDemo.jsx` |

**Inconsistent naming:**
- `org_name` vs `company_name` vs `site_company_name` — all refer to the same concept in different parts of the form flow
- `account_id` vs `accountId` — snake_case from backend leaks into frontend state inconsistently
- `site_id` vs `siteId` — same issue
- `loadingWorkspace` vs `loading` vs `savingNotes` vs `savingRatingFeedback` — loading states use different naming patterns per page

---

## 5. Backend Architecture Analysis

### Route/API Structure

All routes are defined in `backend/main.py` as top-level `@app.get`/`@app.post` decorators. There is no router separation (no `APIRouter`). All routes are in one flat file.

**Route groupings (conceptual, not structural):**

| Group | Routes |
|---|---|
| Frontend config | `GET /api/frontend-config` |
| Auth | `POST /api/auth/check-email`, `POST /api/auth/request-otp`, `POST /api/auth/verify-otp`, `POST /api/auth/logout` |
| Signup aliases | `POST /api/signup/request-otp`, `POST /api/signup/verify-otp`, `POST /api/accounts/new-user` |
| Onboarding | `POST /api/onboarding/complete` |
| Workspace | `POST /api/workspace/state` |
| Sites | `POST /api/account-sites` |
| Customer sites | `POST /api/customer-sites/notes`, `POST /api/customer-sites/rating` |
| Pre-assessment | `POST /api/pre-assessment/request` |
| Credits | `POST /api/credits/usage` |
| Billing | `POST /api/billing/invoices` |
| Debug | `GET /api/debug/google-status` |
| Address validation | `POST /api/address-validation/check` |
| SPA fallback | `GET /{full_path}` |

In addition, `register_vercel_service_api_aliases()` automatically creates `/route` aliases for every `/api/route` to support the Vercel service routing architecture.

### Service Layer Usage

**There is no service layer.** All business logic is written as free async functions in `main.py`:

- `build_workspace_state()`, `build_workspace_payload()`
- `upsert_customer()`, `upsert_account()`
- `insert_account_site_if_missing()`
- `ensure_customer_site_assignment()`
- `find_duplicate_account_site()`
- `insert_billing_usage()`
- `get_customer_usage_state()`
- `send_pre_assessment_approval_email()`
- `send_slack_pre_assessment_notification()`

These are business logic functions that belong in a service layer. They currently live in the same file as route handlers.

### Repository/Database Access Pattern

There is **no repository pattern**. Database access is done through a custom `SupabaseAdmin` class:

```python
class SupabaseAdmin:
    async def request(self, method, path, *, params, json_body, headers): ...
```

This class wraps Supabase's PostgREST REST API using `httpx.AsyncClient`. It makes raw HTTP calls to `/rest/v1/<table_name>` endpoints.

Database access functions (`find_customer_by_email`, `find_account_by_id`, `list_customer_sites`, etc.) are free functions that accept a `db: SupabaseAdmin` parameter. They are defined in `main.py` — not in a separate repository module.

**Every route handler instantiates a new `SupabaseAdmin` per request** via `get_admin_db()`, which creates a new `SupabaseAdmin()` instance. This is cheap but structurally wrong — it should be a FastAPI dependency.

### Validation/Schema Handling

Input validation uses simple helper functions defined in `main.py`:

- `clean_required(raw, label)` — strips and asserts non-empty
- `clean_optional(raw)` — strips
- `assert_work_email(email)` — validates work email format and blocklist
- `normalize_domain(raw)` — normalizes company domain
- `clean_rating_value(raw, label)` — validates 1–5 integer rating
- `parse_site_input(body)` — parses site fields from request body

**There are no Pydantic request models.** All routes accept `body: dict[str, Any] = Body(default={})` — fully untyped. Validation errors are caught and re-raised as `HTTPException(status_code=422)`.

This means FastAPI cannot auto-generate accurate OpenAPI schemas for request bodies.

### Error Handling

- All route handlers wrap business logic in `try/except ValueError` → `HTTPException(status_code=422)`
- HTTPExceptions raised in helper functions propagate correctly
- `infer_error_status()` tries to determine 422 vs 500 from error message text — fragile
- No global exception handler (`@app.exception_handler`)
- No structured error response shape — errors are `{"detail": "message string"}`
- Supabase errors are extracted from response body via `_extract_supabase_error()`

### Logging

There is **no structured logging**. The codebase uses:
- `print(f"Pre-assessment approval email failed: {email_exc}")` — bare print
- `print(f"Slack notification failed: {slack_exc}")` — bare print
- `print("SLACK_WEBHOOK_URL not set; skipping Slack notification")` — bare print

No `logging` module, no log levels, no structured log format. All output goes to stdout.

### Auth Middleware/Dependencies

**There is no auth middleware or auth dependency injection.**

- The `access_token` httpOnly cookie is set on login (`/api/auth/verify-otp`) but is **never read or validated** in any subsequent protected route
- Routes identify the user by reading `email` from the request body
- The only enforcement is that the email must pass `assert_work_email()` validation, and the `find_customer_by_email()` lookup must return a result
- Any caller who knows a valid customer email can call any protected endpoint
- The `CORS` config allows `localhost:5173` and `localhost:3000` only — adequate for development

**This is the most critical security gap in the backend.**

### Background/Agent Workflow Structure

There is no background job system in this codebase. When a pre-assessment is requested:

1. The backend marks `is_report_ready = False` on the customer site assignment
2. Sends a confirmation email via Resend
3. Sends a Slack notification to the internal team
4. Returns `status: "running"`

Report generation happens outside this codebase entirely. When the report is ready (presumably by a separate system), `is_report_ready` is set to `True` and `report_metadata` is populated on `automatisor_customer_sites`. There is no webhook endpoint in this codebase to receive the callback — that mechanism is not visible here.

### Whether Business Logic Is Placed Correctly

No. Business logic is scattered throughout `main.py` as a mixture of:
- Free helper functions (e.g., `build_workspace_state`, `upsert_customer`)
- Inline logic within route handlers (e.g., the pre-assessment request handler is 80+ lines)
- Utility functions (e.g., `clean_required`, `normalize_email`, `is_truthy_flag`) that are mixed in with DB access and email logic

### Duplicate Patterns / Messy Files

- `SupabaseAdmin.request()` is called directly in every helper function — there is no ORM or query builder abstraction
- `normalize_domain()` is defined **twice**: once in `main.py` and once in `address_validator.py`
- Route aliases (`/api/signup/request-otp`, `/api/accounts/new-user`) are just Python function wrappers that delegate — fine, but adds confusion
- The `register_vercel_service_api_aliases()` function dynamically registers every `/api/...` route without the `/api` prefix at startup — this is not obvious and easy to miss

---

## 6. API Contract Analysis

### Main API Endpoints

| Method | Path | Purpose | Auth Required |
|---|---|---|---|
| `GET` | `/api/frontend-config` | Returns Google Maps API key | No |
| `GET` | `/api/debug/google-status` | Returns API key metadata | No |
| `POST` | `/api/address-validation/check` | Validates company at address | No |
| `POST` | `/api/auth/check-email` | Checks if email is new/existing | No |
| `POST` | `/api/auth/request-otp` | Sends OTP | No |
| `POST` | `/api/auth/verify-otp` | Verifies OTP, sets cookie | No |
| `POST` | `/api/auth/logout` | Deletes cookie | No |
| `POST` | `/api/onboarding/complete` | Completes onboarding | Email in body |
| `POST` | `/api/workspace/state` | Returns workspace state | Email in body |
| `POST` | `/api/account-sites` | Adds a facility site | Email in body |
| `POST` | `/api/customer-sites/notes` | Saves notes | Email in body |
| `POST` | `/api/customer-sites/rating` | Saves report rating | Email in body |
| `POST` | `/api/pre-assessment/request` | Requests pre-assessment | Email in body |
| `POST` | `/api/credits/usage` | Returns credit usage | Email in body |
| `POST` | `/api/billing/invoices` | Returns invoices | Email in body |

### Request/Response Patterns

**Requests:** All request bodies are flat JSON objects. Fields use `snake_case`. No consistent envelope.

**Success responses:** Flat JSON objects. Different routes return different shapes:
- `/api/auth/verify-otp` returns `{status, user_id, email, user_mode, next_step, customer_id, ...}`
- `/api/workspace/state` returns `{email, user_mode, next_step, customer_id, account_id, sites, accounts, ...}`
- `/api/pre-assessment/request` returns `{status, email, account_id, site_id, credits_used_total, ...}`
- `/api/credits/usage` returns `{billing_anchor_date, billing_periods: [...]}`
- `/api/billing/invoices` returns `{invoices: [...]}`

**Error responses:** Always `{"detail": "error message string"}` from FastAPI's default exception handler.

### Whether Responses Follow a Consistent Shape

**No.** There is no standard response envelope. Each endpoint returns a bespoke object. The only consistency is:
- `status` field on write operations (e.g., `"verified"`, `"site_saved"`, `"running"`, `"saved"`)
- `detail` field on error

### Whether Errors Follow a Consistent Shape

Partially. All errors are `{"detail": "string"}` with HTTP status codes. However:
- Some routes distinguish 422 vs 500; others always raise 422
- `infer_error_status()` infers the right status code from error message content — fragile and not reliable

### Endpoints That Break Structure or Naming Consistency

- `POST /api/credits/usage` and `POST /api/billing/invoices` — both are reads but use `POST` because the email is in the body rather than a query param. This is non-standard; these should be `GET` with email as a query param, or auth should be cookie-based so the body isn't needed.
- `POST /api/workspace/state` — a read operation using POST for the same reason.
- `GET /api/frontend-config` — exposes a backend secret key to the browser. This is a design issue, not just a naming issue.
- `/api/debug/google-status` — a debug endpoint with no auth or env-gate. Should not exist in production.

---

## 7. Database/Data Model Analysis

### Main Tables/Entities

Based on all observed queries in `main.py`:

| Table | Key Columns | Purpose |
|---|---|---|
| `automatisor_customer` | `customer_id`, `email`, `first_name`, `last_name`, `full_name`, `designation`, `company_name`, `company_domain`, `email_verified`, `email_verified_at`, `last_login_at`, `metadata` | User profile |
| `accounts` | `account_id`, `company_name`, `account_domain` | Company/organization entity |
| `account_sites` | `site_id`, `account_id`, `full_address`, `company_name`, `street`, `city`, `state`, `zip`, `country`, `is_archived`, `metadata`, `created_at` | Physical facility site |
| `automatisor_customer_sites` | `customer_site_id`, `customer_id`, `site_id`, `account_id`, `assigned_via`, `metadata`, `notes`, `report_metadata`, `rating_metadata`, `is_report_ready`, `created_at`, `updated_at` | Junction: customer ↔ site, with report state |
| `automatisor_billing` | `customer_id`, `account_id`, `site_id`, `usage_type`, `credits_used`, `metadata`, `created_at` | Credit usage events |
| `automatisor_invoices` | `invoice_id`, `invoice_number`, `invoice_date`, `amount_usd`, `status`, `pdf_url`, `payment_url`, `period_start`, `period_end`, `customer_id` | Invoice records |

### Naming Consistency

- Tables use mixed naming: `accounts` (no prefix) vs `automatisor_customer`, `automatisor_customer_sites`, `automatisor_billing` (prefix). This suggests `accounts` is a shared or legacy table.
- Column naming is consistent snake_case
- `automatisor_customer_sites` plays the role of both a junction table (customer ↔ site) and a container for report metadata, notes, and ratings — it has grown beyond a pure join table

### Where Database Queries Are Written

**All queries are in `main.py`** as free async functions that call `db.request()` directly against Supabase PostgREST paths like `/rest/v1/automatisor_customer_sites`. There is no repository module, no ORM model file, and no data access layer.

### Risky Patterns

1. **No transactions** — multi-step operations (e.g., `upsert_account` + `upsert_customer` + `insert_account_site_if_missing` + `insert_billing_usage`) are not wrapped in a database transaction. If any step fails, partial state is written.
2. **`find_duplicate_account_site` fetches up to 5000 rows** and performs duplicate detection in Python — this will degrade as site counts grow.
3. **`list_customer_accounts` makes two sequential DB calls** — first fetching assignment rows, then fetching account rows. As accounts grow, this could become a performance bottleneck.
4. **`report_metadata` stored as JSONB in `automatisor_customer_sites`** — the full structured report is stored directly on the junction table row. This couples report data tightly to the customer-site relationship.
5. **Queries use string interpolation for `IN` clauses**: `f"in.({','.join(ids)})"` — this is safe only because IDs are UUIDs (no user input), but it's fragile.

### Missing Separation Between Models, Services, and Routes

Everything is in one file. There are no Pydantic models, no service classes, no repository classes. The codebase is a flat procedural script organized as a FastAPI app.

---

## 8. Security and Reliability Observations

### Hardcoded Secrets

No secrets are hardcoded. All keys are loaded from `.env` via `python-dotenv`. This is correct.

### Unsafe Frontend Exposure of Backend Keys

**Critical issue:**

```python
@app.get("/api/frontend-config")
async def frontend_config() -> dict[str, Any]:
    return {"google_maps_api_key": GOOGLE_MAPS_API_KEY}
```

The `GOOGLE_MAPS_API_KEY` is served to the browser. Anyone who opens DevTools can extract this key. Google Maps API keys should be restricted by HTTP referrer in the Google Cloud Console (domain restriction), but the key itself should not be exposed this way. If referrer restrictions are not configured, this key can be used by anyone for free.

**Additionally:**

```python
@app.get("/api/debug/google-status")
async def debug_google_status() -> dict[str, Any]:
    return {
        "google_maps_api_key_present": bool(key),
        "google_maps_api_key_length": len(key),
        "google_maps_api_key_prefix": key[:8] if key else "",
        "google_maps_api_key_suffix": key[-4:] if key else "",
    }
```

This exposes the key prefix and suffix publicly with no auth. This endpoint should not exist in production.

### Missing Auth Checks

**Critical issue:** The `access_token` httpOnly cookie is set on login but **never read or validated** in any route handler. Protected routes identify users by `email` in the request body. There is no FastAPI dependency that validates the cookie or JWT token.

This means:
- Any caller who knows a valid customer email can call `/api/account-sites`, `/api/customer-sites/notes`, `/api/pre-assessment/request`, etc.
- There is no CSRF protection (though `SameSite=lax` on the cookie helps partially)
- The security model relies entirely on obscurity of valid email addresses

### Weak Validation

- All route bodies use `dict[str, Any]` — no Pydantic models, so FastAPI does not validate types automatically
- `parse_site_input()` accepts many aliased field names (`full_address` or `site_address`, `street` or `site_street`, etc.) — permissive input aliasing
- `clean_optional()` returns an empty string for missing fields with no further validation
- `is_truthy_flag()` accepts `"1"`, `"true"`, `"yes"`, `"on"` for boolean-like values — overly permissive

### Poor Error Handling

- Silent `except` blocks in frontend: `catch { // Ignore. }` in `loadSession`, `saveSession`, `clearSession`, `clearPreAssessmentContext`, `saveReportContext`, `loadReportContext`
- Backend uses bare `print()` for errors (email failure, Slack failure) with no re-raise — these failures are completely invisible in production without log aggregation
- `infer_error_status()` infers HTTP status from error message content — fragile

### Missing Rate Limits

There are no rate limits on any endpoint. The OTP endpoints (`/api/auth/request-otp`, `/api/auth/verify-otp`) are unprotected and could be abused for spam or brute force. No `slowapi` or similar middleware is configured.

### Unclear Permissions

There is no role-based access control. All authenticated users (identified by email) have the same permissions. There is no concept of admin vs. regular user in the route layer.

### Risky Environment Variable Usage

- `COOKIE_SECURE` can be set to `"false"` via env var — this disables secure cookies and should only ever be used in development
- `SERVER_DRY_RUN` can be triggered via `--dry` CLI arg or `AUTOMATISOR_DRY=1` env var, or even via a `dry: true` field in any request body — this allows bypassing all actual DB writes and email sends from any client request, which is a risk in production
- `GOOGLE_MAPS_API_KEY` exposed via API endpoint (see above)

---

## 9. Code Quality Issues

### Files That Are Too Large

| File | Approximate Lines | Problem |
|---|---|---|
| `frontend/src/App.jsx` | ~4600+ | Contains every page, component, hook, and utility function for the entire frontend |
| `backend/main.py` | ~1900+ | Contains every route, service function, DB access function, utility, and email template |

### Repeated Logic

| Logic | Repeated Where |
|---|---|
| `SESSION_KEY`, `loadSession()`, `fetchJson()`, `useRequireSession()` | `App.jsx`, `BillingPage.jsx`, `CreditsPage.jsx` |
| `formatInvoiceDate()`, `formatCurrency()` | `BillingPage.jsx`, `BillingPageDemo.jsx` |
| `formatDateTimeEST()`, `formatPeriodDate()` | `CreditsPage.jsx`, `CreditsPageDemo.jsx` |
| `normalize_domain()` | `backend/main.py`, `backend/address_validator.py` |
| Report confidence logic | `REPORT_NO_CONFIDENCE_PATHS`, `shouldHideReportConfidence()`, `shouldPersistReportAcrossConfidenceFilters()` — all inline in `App.jsx` with no documentation |

### Unclear Naming

- `NewUserPage` — the component handles both new users AND existing users (email + OTP for returning users). The name is misleading.
- `useRequireSession` — returns `[session, setSession]` as a tuple but the name implies a guard only
- `buildPendingSiteFromInput` — the function merges form state, validation state, and a resolved address into a route state object. The name understates its complexity.
- `org_name` and `org_domain` vs `customer_company_name`/`customer_company_domain` — two separate concepts (the site's company vs. the user's company) that are confusingly named, especially in the onboarding form which has both
- `text.md` in the root — unknown file, appears to be scratch notes

### Dead/Unused Files

- `text.md` — root-level file with unknown purpose
- `backend/requirements.txt` — redundant since Poetry (`pyproject.toml`) is the declared source of truth per user preference
- `frontend/src/BillingPageDemo.jsx` and `frontend/src/CreditsPageDemo.jsx` — demo pages with hardcoded data accessible at `/demo/credits` and `/demo/billing`. These are not linked from anywhere in the UI but exist in production routing.
- `HomePage` component inside `App.jsx` — defines a full marketing landing page with a nav, hero section, and "How it works" section, but `/` immediately redirects to `/auth`. This component is never rendered.

### Mixed Responsibilities

- `App.jsx` — page rendering, state management, API calls, report parsing, Google Maps initialization, address normalization, session management, and utility formatting
- `main.py` — route definitions, business logic, database queries, email template generation, Slack notification construction, address validation orchestration, and app startup configuration
- `automatisor_customer_sites` table — junction table, but also stores report metadata, notes, rating metadata, and report readiness state

### Inconsistent Folder Organization

- `backend/sample-report/data_structure.json` is stored under `backend/` but is imported directly into the **frontend** (`App.jsx` line 19: `import brWilliamsSampleReport from "../../backend/sample-report/data_structure.json"`). Frontend code directly imports from the backend folder — this is a clear boundary violation.
- Demo page files (`BillingPageDemo.jsx`, `CreditsPageDemo.jsx`) live alongside production pages with no `demo/` subfolder separation.

### Inconsistent Imports

- `App.jsx` imports `brWilliamsSampleReport` from `../../backend/sample-report/data_structure.json` — cross-boundary import from backend into frontend
- `backend/main.py` has a try/except import block for its own sibling modules:
  ```python
  try:
      from .address_normalization import ...
  except ImportError:
      from address_normalization import ...
  ```
  This exists to handle both package-style (`python -m uvicorn backend.main:app`) and flat-style (`python main.py`) execution — a sign that the module system is unclear.

### Areas That Will Become Hard to Scale

1. **Single-file frontend** — adding any new page or component requires editing a 4600-line file. Merge conflicts will become daily friction.
2. **No API client module** — every new page must re-implement `fetchJson`, session loading, and error handling.
3. **No Pydantic models** — adding request validation to any route requires manually adding `clean_*` calls rather than declaring a schema.
4. **Report rendering logic in App.jsx** — the report structure config (`report_section_structure.json`) and all rendering logic are tightly coupled. Changing the report format requires touching a 4600-line file.
5. **No auth middleware** — adding role-based access or proper token validation requires changing every route handler.
6. **`automatisor_customer_sites` as a catch-all** — this table now holds report data, notes, ratings, and request metadata. Every new report feature adds more columns to this junction table.

---

## 10. Suggested Rulebook Sections

### Frontend Rules

| Section | What to Define |
|---|---|
| **Folder structure rules** | Where pages, components, hooks, utils, types, and API clients live; one component per file rule; no cross-boundary imports (frontend must not import from `backend/`) |
| **Component rules** | Max component size (suggest ~200 lines); separation of display components vs. container components; when to use `forwardRef`; no business logic inside JSX |
| **API call rules** | One shared `api/` module; no inline `fetch` outside the api layer; all calls return typed objects; all calls handle errors consistently |
| **State management rules** | When to use local `useState` vs. lift state; no global mutable variables; session state must only be read/written through defined session utilities |
| **Naming rules** | PascalCase for components; camelCase for functions and variables; consistent `form`, `loading`, `error` variable naming per page; no ambiguous `org_name` vs `company_name` — pick one |
| **Error/loading rules** | Centralized error display component; no silent catch blocks; loading state must always be cleared in finally; all async operations must show feedback |
| **Auth rules** | `useRequireSession` must be the only hook used for auth guard; session must never be read directly from sessionStorage outside the session module; session refresh on app load |
| **Styling rules** | CSS class naming convention (BEM or component-scoped); no inline styles except for dynamic values like Maps embed dimensions; all new styles go in the component's own CSS file |

### Backend Rules

| Section | What to Define |
|---|---|
| **Folder structure rules** | `routes/`, `services/`, `repositories/`, `schemas/`, `utils/` folders required; no business logic in route handlers; no DB calls in route handlers |
| **Route rules** | Routes accept Pydantic models; routes call service functions only; no more than ~20 lines per route handler; HTTP methods must match semantics (GET for reads) |
| **Service rules** | Service functions own business logic; services call repositories; services do not call `db.request()` directly; services handle notification side effects |
| **Repository/database rules** | All DB access via repository functions; no raw `db.request()` calls in route handlers or services; repository functions return typed dicts or dataclasses; duplicate detection must not fetch unbounded rows |
| **Schema/validation rules** | All request bodies must be Pydantic models; all response shapes must be Pydantic models; no `dict[str, Any]` as route input; `clean_*` utils used only inside Pydantic validators |
| **Error handling rules** | One global exception handler; all errors return `{"error": {"code": str, "message": str}}`; no status inference from message content; no bare `print()` for errors |
| **Logging rules** | Use Python `logging` module; no `print()` in production code; all external call failures must be logged at WARNING or ERROR level |
| **Auth/security rules** | Every protected route must validate `access_token` cookie via FastAPI dependency; no email-from-body auth; `debug/` endpoints must be gated by env var or removed; `GOOGLE_MAPS_API_KEY` must not be served to browser; `dry` flag must be server-side only, not client-controllable |
| **AI/agent workflow rules** | If a job queue or agent layer is added, define a webhook endpoint contract; `is_report_ready` must only be set via an authenticated internal callback, not by client request |

---

## 11. Non-Negotiable Rules You Recommend

The following rules are strict requirements for this codebase going forward:

### Security
1. **Never expose backend API keys to the browser.** The `GOOGLE_MAPS_API_KEY` must not be returned from any API endpoint. Embed it in the build environment via Vite's `VITE_` prefix or restrict usage to backend-only.
2. **All protected routes must validate the `access_token` cookie.** Implement a FastAPI dependency (e.g., `get_current_customer`) that reads and validates the httpOnly cookie. Remove the email-in-body auth pattern.
3. **Remove `/api/debug/google-status` from production.** Gate it behind an env var (`DEBUG=true`) or delete it entirely.
4. **The `dry` request body flag must be removed from the API contract.** Dry run mode must be a server-side environment toggle only.
5. **All secrets must come from environment variables.** Never hardcode any key, URL, or credential. *(Already followed — maintain this.)*

### Architecture
6. **No direct database calls inside route handlers.** Route handlers call service functions. Service functions call repository functions. Repository functions call the DB layer.
7. **All backend route bodies must use Pydantic models.** No `dict[str, Any]` as a route input type.
8. **All API responses must follow one standard shape.** Success: `{"data": {...}}`. Error: `{"error": {"code": str, "message": str}}`.
9. **All backend errors must be logged** using Python's `logging` module, not `print()`.
10. **No business logic inside frontend components.** Logic that transforms data (report parsing, confidence scoring, address normalization) must live in utility modules or custom hooks.

### Code Organization
11. **No cross-boundary imports.** The frontend must not import files from the `backend/` folder. The `backend/sample-report/data_structure.json` must be moved to `frontend/src/data/`.
12. **Session utilities must not be duplicated.** `SESSION_KEY`, `loadSession`, `saveSession`, `fetchJson`, and `useRequireSession` must live in one shared module (e.g., `frontend/src/lib/session.js` and `frontend/src/lib/api.js`). All pages import from there.
13. **All large components must be split into smaller components.** No single component file should exceed ~300 lines. `GoogleAddressPicker`, `StructuredPreAssessmentReport`, and `NewUserPage` must each become their own files/folders.
14. **Demo pages must be separated from production code.** Demo routes must either be removed or placed in a clearly marked `demo/` folder, never mixed with production page imports.
15. **Rate limiting must be applied to OTP endpoints** before going to production scale. Use `slowapi` or equivalent.

---

## 12. Final Summary

### What Is Already Good

- **`address_validator.py` and `address_normalization.py`** are clean, well-isolated, and properly tested. This is the best-structured code in the codebase.
- **httpOnly cookie auth flow** is architecturally correct. Setting the `access_token` as httpOnly/SameSite=lax is the right approach.
- **Work email blocklist** is thorough and correctly enforced at the backend.
- **HTML escaping in email templates** is done correctly via `escape_html()`.
- **Vercel deployment config** is clean and well-structured via `vercel.json`.
- **Poetry for dependency management** is correct and the `pyproject.toml` is clean.
- **Error feedback in forms** is consistently surfaced — every form shows errors to users.
- **Dry-run mode** is useful for testing, even if its current client-controllable implementation is a security concern.
- **Address validation with candidate suggestions** is a thoughtful UX feature with a clean implementation.

### What Is Messy

- **`App.jsx` is a 4600+ line monolith** containing every component, page, hook, and utility in the entire frontend. This is the single biggest quality problem.
- **`main.py` is a 1900+ line monolith** with no separation between routes, services, repositories, and utilities.
- **Session utilities are copy-pasted** across `App.jsx`, `BillingPage.jsx`, and `CreditsPage.jsx`.
- **Formatting helpers are copy-pasted** across `BillingPage.jsx`, `BillingPageDemo.jsx`, `CreditsPage.jsx`, and `CreditsPageDemo.jsx`.
- **`normalize_domain` exists in two files** — `main.py` and `address_validator.py`.
- **The frontend imports directly from the backend folder** — a clear boundary violation.
- **The `NewUserPage` component** handles three distinct UI flows (email, OTP, onboarding) with 20+ state variables in a single component.
- **Dead code**: `HomePage` component is defined but unreachable; `text.md` exists with no purpose; `requirements.txt` is redundant.

### What Needs Rules Urgently

1. **Auth security** — the missing cookie validation on protected routes is critical. Must be fixed before scaling.
2. **Google Maps API key exposure** — must be fixed immediately.
3. **Debug endpoint in production** — must be gated or removed.
4. **Dry run via request body** — must be made server-side-only.
5. **Frontend duplication** — `fetchJson`, `SESSION_KEY`, `useRequireSession` being copy-pasted means any security fix to auth handling must be applied in 3+ places. A shared module is urgent.
6. **No Pydantic models** — adding any new input validation currently requires writing custom `clean_*` helper functions instead of a declared schema.

### What Can Be Improved Later

- Splitting `App.jsx` into a proper component/page folder structure (high priority but not a security issue)
- Adding React Query or SWR for server state management
- Splitting `main.py` into `routes/`, `services/`, `repositories/` folders
- Adding TypeScript to the frontend
- Adding a proper logging system with structured logs (Loguru or stdlib `logging`)
- Moving Google Maps loading to a proper hook with caching
- Adding rate limiting to OTP endpoints
- Adding end-to-end tests for the auth + pre-assessment flow
- Replacing the custom `SupabaseAdmin` HTTP wrapper with proper use of `supabase-py` for database operations
- Defining a standard API response envelope
- Adding a webhook endpoint for report completion callbacks from the external agent system

---

*End of report.*
