import os
import re
import json
import base64
import binascii
import hmac
import hashlib
import asyncio
import html
import time
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import stripe
import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from supabase import Client, create_client

try:
    from .address_normalization import canonical_zip, normalize_full_address, normalize_state, normalize_street_line
    from .address_validator import validate_company_site
    from .chat import router as chat_router
except ImportError:
    from address_normalization import canonical_zip, normalize_full_address, normalize_state, normalize_street_line
    from address_validator import validate_company_site
    from chat import router as chat_router

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent

# Ensure local development env vars are available when running uvicorn directly.
load_dotenv(ROOT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

PORT = int(os.getenv("PORT", "3000"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
RECOMMENDATION_SYSTEM_URL = os.getenv("RECOMMENDATION_SYSTEM_URL", "").strip().rstrip("/")
RECOMMENDATION_WORKER_SECRET = (
    os.getenv("RECOMMENDATION_WORKER_SECRET", "").strip()
    or os.getenv("INTERNAL_API_KEY", "").strip()
)
VERCEL_ENV = os.getenv("VERCEL_ENV", "")
IS_PRODUCTION = VERCEL_ENV == "production" or os.getenv("ENV", "") == "production"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true" if IS_PRODUCTION else "false") != "false"
SERVER_DRY_RUN = "--dry" in os.sys.argv or os.getenv("AUTOMATISOR_DRY") == "1"

def _parse_cors_origins(raw: str | None) -> list[str]:
    if not raw:
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


CORS_ALLOW_ORIGINS = _parse_cors_origins(os.getenv("CORS_ALLOW_ORIGINS") or os.getenv("ALLOWED_ORIGINS"))

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
# Accept both STRIPE_PUBLISHABLE_KEY (preferred, single .env) and
# VITE_STRIPE_PUBLISHABLE_KEY (legacy frontend/.env name) so deployments
# using either convention work without changes.
STRIPE_PUBLISHABLE_KEY = (
    os.getenv("STRIPE_PUBLISHABLE_KEY")
    or os.getenv("VITE_STRIPE_PUBLISHABLE_KEY")
    or ""
)
PRICE_PER_CREDIT_USD_CENTS = 5000  # $50.00 per credit

stripe.api_key = STRIPE_SECRET_KEY

# Share feature
APP_BASE_URL = os.getenv("APP_BASE_URL", "")
SHARE_TOKEN_SECRET = os.getenv("SHARE_TOKEN_SECRET", "")


def get_app_base_url(request: Request | None = None) -> str:
    """Resolve the public app URL used in share links and emails."""
    configured = str(os.getenv("APP_BASE_URL") or APP_BASE_URL or "").strip().rstrip("/")
    if configured:
        return configured
    if request is not None:
        origin = clean_optional(request.headers.get("origin"))
        if origin.startswith(("http://", "https://")):
            return origin.rstrip("/")
        referer = clean_optional(request.headers.get("referer"))
        if referer:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if not IS_PRODUCTION:
        return "http://localhost:5173"
    return ""

SIGNUP_CREDITS = 2
PRE_ASSESSMENT_PRICE = 2
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

# BR Williams sample site — fixed IDs from account_sites
SAMPLE_SITE_ID = "d80e7532-2253-4c53-a31b-90e05dfb98d8"
SAMPLE_ACCOUNT_ID = "767371c1-6e17-4401-91a1-b0808610cf25"
_SAMPLE_REPORT_DIR = BACKEND_DIR / "sample-report"

_sample_site_data_cache: dict[str, Any] | None = None


def _load_sample_site_data() -> dict[str, Any]:
    """Load BR Williams sample report data from repo files (cached after first read)."""
    global _sample_site_data_cache
    if _sample_site_data_cache is not None:
        return _sample_site_data_cache
    with open(_SAMPLE_REPORT_DIR / "br_williams_high.json", encoding="utf-8") as f:
        high = json.load(f)
    with open(_SAMPLE_REPORT_DIR / "br_williams_all.json", encoding="utf-8") as f:
        all_ = json.load(f)
    with open(_SAMPLE_REPORT_DIR / "data_structure.json", encoding="utf-8") as f:
        structure = json.load(f)
    _sample_site_data_cache = {
        "report_context_high": high,
        "report_context_all": all_,
        "report_metadata": structure,
    }
    return _sample_site_data_cache


async def ensure_sample_site_row(db: "SupabaseAdmin", customer_id: str) -> dict[str, Any]:
    """Return the customer's sample site row, creating it if absent."""
    existing = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,site_id",
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{SAMPLE_SITE_ID}",
            "assigned_via": "eq.sample_site",
            "limit": "1",
        },
    )
    if existing:
        return existing[0]
    data = _load_sample_site_data()
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_sites",
        json_body={
            "customer_id": customer_id,
            "site_id": SAMPLE_SITE_ID,
            "account_id": SAMPLE_ACCOUNT_ID,
            "assigned_via": "sample_site",
            "is_report_ready": True,
            "report_context_high": data["report_context_high"],
            "report_context_all": data["report_context_all"],
            "report_metadata": data["report_metadata"],
        },
        headers={"Prefer": "return=representation"},
    )
    return created[0]

DISALLOWED_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "yahoo.co.uk",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "pm.me",
    "gmx.com",
    "mail.com",
    "zoho.com",
    "yandex.com",
    "qq.com",
    "rediffmail.com",
    "fastmail.com",
    "hey.com",
}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat_router)

SERVICE_API_PREFIXES = (
    "/account-sites",
    "/accounts",
    "/address-validation",
    "/billing",
    "/chat",
    "/companies",
    "/credits",
    "/customer-context",
    "/customer-sites",
    "/debug",
    "/frontend-config",
    "/onboarding",
    "/pre-assessment",
    "/signup",
)

USAGE_TYPE_LABELS: dict[str, str] = {
    "pre_assessment_request": "Pre-Assessment Request",
}

EVENT_TYPE_WISH_LIST = "wish_list"
EVENT_TYPE_USER_ADDED_COMPANY = "user_added_company"

CUSTOMER_CONTEXT_SELECT = (
    "customer_context_id,customer_id,site_id,account_id,event_type,metadata,notes,created_at,updated_at"
)


@app.middleware("http")
async def normalize_vercel_service_api_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    if not path.startswith("/api/") and path.startswith(SERVICE_API_PREFIXES):
        request.scope["path"] = f"/api{path}"
    return await call_next(request)


class SupabaseAdmin:
    def __init__(self) -> None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        self.base_url = SUPABASE_URL.rstrip("/")
        self.headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {**self.headers, **(headers or {})}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_body,
                headers=request_headers,
            )
        if response.is_error:
            detail = _extract_supabase_error(response)
            raise HTTPException(status_code=infer_error_status(detail), detail=detail)
        if not response.text:
            return None
        return response.json()


def get_admin_db() -> SupabaseAdmin:
    return SupabaseAdmin()


def get_auth_headers() -> dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }


def get_supabase_auth_url() -> str:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
    return f"{SUPABASE_URL.rstrip('/')}/auth/v1"


RESEND_API_URL = "https://api.resend.com/emails"
_resend_sync_client: httpx.Client | None = None
SUPABASE_AUTH_SEND_TIMEOUT = httpx.Timeout(45.0, connect=10.0, read=45.0, write=10.0, pool=10.0)
SUPABASE_AUTH_VERIFY_TIMEOUT = httpx.Timeout(20.0, connect=10.0, read=20.0, write=10.0, pool=10.0)


def _get_resend_sync_client() -> httpx.Client:
    """Sync Resend client bound to IPv4 (avoids Windows async/IPv6 DNS failures)."""
    global _resend_sync_client
    if _resend_sync_client is None:
        transport = httpx.HTTPTransport(local_address="0.0.0.0")
        _resend_sync_client = httpx.Client(transport=transport, timeout=30.0)
    return _resend_sync_client


def _require_resend_config() -> str:
    from_email = str(os.getenv("RESEND_FROM_EMAIL") or RESEND_FROM_EMAIL or "").strip()
    api_key = str(os.getenv("RESEND_API_KEY") or RESEND_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("Missing RESEND_API_KEY")
    if not from_email:
        raise RuntimeError("Missing RESEND_FROM_EMAIL")
    return from_email


def _post_resend_email_sync(payload: dict[str, Any]) -> httpx.Response:
    """Send email via Resend using sync HTTP (same path for all transactional emails)."""
    api_key = str(os.getenv("RESEND_API_KEY") or RESEND_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("Missing RESEND_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return _get_resend_sync_client().post(RESEND_API_URL, headers=headers, json=payload)
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise RuntimeError(
                "Could not reach email service (api.resend.com). Check your internet connection and try again."
            ) from exc
    raise RuntimeError("Failed to send email") from last_exc


async def post_resend_email(payload: dict[str, Any]) -> httpx.Response:
    return await asyncio.to_thread(_post_resend_email_sync, payload)


async def _deliver_resend_email(payload: dict[str, Any], *, failure_label: str) -> None:
    """Shared delivery helper used by pre-assessment, report-share, and chat-share emails."""
    response = await post_resend_email(payload)
    if response.is_error:
        raise RuntimeError(f"{failure_label}: {response.text}")


def get_auth_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def _extract_supabase_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text or "Request failed"
    if isinstance(payload, dict):
        return (
            payload.get("msg")
            or payload.get("message")
            or payload.get("detail")
            or payload.get("error_description")
            or payload.get("error")
            or payload.get("details")
            or "Request failed"
        )
    return response.text or "Request failed"


def normalize_email(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _parse_trusted_bypass_emails(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {normalize_email(part) for part in raw.split(",") if part.strip()}


TRUSTED_AUTH_BYPASS_EMAILS = _parse_trusted_bypass_emails(os.getenv("TRUSTED_AUTH_BYPASS_EMAILS"))


def is_trusted_bypass_email(email: str) -> bool:
    return normalize_email(email) in TRUSTED_AUTH_BYPASS_EMAILS


def normalize_domain(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if "://" in value:
        try:
            from urllib.parse import urlparse

            value = urlparse(value).hostname or ""
        except Exception:
            value = ""
    value = re.sub(r"^\.+", "", value)
    if value.startswith("www."):
        value = value[4:]
    value = re.sub(r"\.+", ".", value).rstrip(".")
    if not value or " " in value or "." not in value:
        raise ValueError("Invalid company domain")
    return value


def email_domain(email: str) -> str:
    normalized = normalize_email(email)
    parts = normalized.split("@")
    return parts[1] if len(parts) == 2 else ""


def is_company_email_address(email: str) -> bool:
    domain = email_domain(email)
    if not domain:
        return False
    return domain not in DISALLOWED_PERSONAL_EMAIL_DOMAINS


def assert_work_email(email: Any) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized or re.search(r"\s", normalized):
        raise ValueError("Invalid work email")
    parts = normalized.split("@")
    if len(parts) != 2:
        raise ValueError("Invalid work email")
    local_part, domain = parts
    if not local_part or not domain or "." not in domain:
        raise ValueError("Invalid work email")
    if domain.startswith(".") or domain.endswith("."):
        raise ValueError("Invalid work email")
    if not is_company_email_address(normalized):
        raise ValueError("Please use your work email address")
    return normalized


def get_share_token_secret() -> str:
    """Read share signing secret at call time (picks up .env changes in local dev)."""
    secret = str(os.getenv("SHARE_TOKEN_SECRET") or SHARE_TOKEN_SECRET or "").strip()
    if secret:
        return secret
    load_dotenv(ROOT_DIR / ".env", override=False)
    load_dotenv(BACKEND_DIR / ".env", override=False)
    return str(os.getenv("SHARE_TOKEN_SECRET") or "").strip()


def encode_share_token(
    recipient_email: str,
    site_id: str,
    shared_by_customer_id: str,
    *,
    session_id: str | None = None,
    share_type: str = "report",
) -> str:
    """Create HMAC-signed token for share links."""
    share_token_secret = get_share_token_secret()
    if not share_token_secret:
        raise HTTPException(status_code=500, detail="Missing SHARE_TOKEN_SECRET")
    payload_data: dict[str, str] = {
        "recipient_email": recipient_email,
        "site_id": site_id,
        "shared_by": shared_by_customer_id,
    }
    if share_type == "chat" and session_id:
        payload_data["share_type"] = "chat"
        payload_data["session_id"] = session_id
    payload = json.dumps(
        payload_data,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(share_token_secret.encode("utf-8"), encoded, hashlib.sha256).digest()
    return f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def decode_share_token(raw_token: Any) -> dict[str, str] | None:
    """Decode and verify HMAC-signed share token."""
    token = str(raw_token or "").strip()
    share_token_secret = get_share_token_secret()
    if not token or not share_token_secret:
        return None
    try:
        encoded, raw_signature = token.split(".", 1)
        expected = hmac.new(share_token_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(raw_signature + "=" * (-len(raw_signature) % 4))
        if not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        recipient_email = assert_work_email(payload.get("recipient_email"))
        site_id = clean_required(payload.get("site_id"), "Site")
        shared_by = clean_optional(payload.get("shared_by"))  # Optional for backward compat
        share_type = clean_optional(payload.get("share_type")) or "report"
        session_id = clean_optional(payload.get("session_id"))
        result = {
            "recipient_email": recipient_email,
            "site_id": site_id,
            "shared_by": shared_by,
            "share_type": share_type,
        }
        if session_id:
            result["session_id"] = session_id
        return result
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def clean_required(raw: Any, label: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError(f"{label} is required")
    return value


def clean_optional(raw: Any) -> str:
    return str(raw or "").strip()


def clean_rating_value(raw: Any, label: str) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} rating must be between 1 and 5") from exc
    if value < 1 or value > 5:
        raise ValueError(f"{label} rating must be between 1 and 5")
    return value


def build_rating_metadata(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "coverage": clean_rating_value(body.get("coverage"), "Coverage"),
        "accuracy": clean_rating_value(body.get("accuracy"), "Accuracy"),
        "value": clean_rating_value(body.get("value"), "Value"),
        "additional_feedback": str(body.get("additional_feedback") or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def is_truthy_flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def is_dry_run_request(request: Request, body: dict[str, Any] | None) -> bool:
    return (
        SERVER_DRY_RUN
        or is_truthy_flag(request.query_params.get("dry"))
        or is_truthy_flag((body or {}).get("dry"))
    )


def infer_error_status(detail: str) -> int:
    if re.search(r"rate limit|too many|security purposes", detail or "", re.I):
        return 429
    if re.search(r"required|Invalid|expired|work email|company domain|OTP|address", detail or "", re.I):
        return 422
    return 500


def parse_site_input(body: dict[str, Any]) -> dict[str, str]:
    full_address = clean_required(body.get("full_address") or body.get("site_address"), "Site address")
    street = clean_optional(body.get("street") or body.get("site_street"))
    city = clean_optional(body.get("city") or body.get("site_city"))
    raw_state = clean_optional(body.get("state") or body.get("site_state"))
    zip_code = clean_optional(body.get("zip") or body.get("site_zip"))
    site_name = clean_optional(body.get("site_name"))
    site_type = clean_optional(body.get("site_type")) or "User-added site"
    normalized_state = normalize_state(raw_state).upper() if raw_state else ""
    return {
        "fullAddress": full_address,
        "street": street,
        "city": city,
        "state": normalized_state,
        "zip": zip_code,
        "country": clean_optional(body.get("country") or body.get("site_country")) or "US",
        "siteName": site_name,
        "siteType": site_type,
    }


def site_insert_row(account_id: str, company_name: str, site: dict[str, str]) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "company_name": company_name,
        "full_address": site["fullAddress"],
        "street": site["street"] or None,
        "city": site["city"] or None,
        "state": site["state"] or None,
        "zip": site["zip"] or None,
        "country": site["country"] or "US",
    }


def compact_validation_evidence(body: dict[str, Any], *, include_justification: bool) -> dict[str, Any]:
    source = body.get("address_validation")
    if not isinstance(source, dict):
        source = {}
    evidence = {
        key: value
        for key, value in source.items()
        if key not in {"justification"}
    }
    request_basis = clean_optional(body.get("request_basis") or evidence.get("request_basis"))
    if request_basis:
        evidence["request_basis"] = request_basis
    selected_candidate = body.get("selected_candidate") or evidence.get("selected_candidate")
    if isinstance(selected_candidate, dict) and selected_candidate:
        evidence["selected_candidate"] = selected_candidate
    if evidence and "checked_at" not in evidence:
        evidence["checked_at"] = datetime.now(timezone.utc).isoformat()
    if include_justification:
        justification = clean_optional(body.get("justification") or source.get("justification"))
        if justification:
            evidence["justification"] = justification
    return evidence


def normalized_structured_site(site_like: dict[str, Any]) -> dict[str, str]:
    street_parts = normalize_street_line(site_like.get("street") or "")
    return {
        "fullAddress": normalize_full_address(site_like.get("fullAddress") or site_like.get("full_address") or ""),
        "street": normalize_full_address(site_like.get("street") or ""),
        "houseNumber": street_parts["house_number"],
        "route": street_parts["route"],
        "city": normalize_full_address(site_like.get("city") or ""),
        "state": normalize_full_address(site_like.get("state") or ""),
        "zip": canonical_zip(site_like.get("zip") or site_like.get("zipCode") or ""),
    }


def same_structured_site(a: dict[str, str], b: dict[str, str]) -> bool:
    return bool(
        a["houseNumber"]
        and a["route"]
        and a["city"]
        and a["state"]
        and a["zip"]
        and a["houseNumber"] == b["houseNumber"]
        and a["route"] == b["route"]
        and a["city"] == b["city"]
        and a["state"] == b["state"]
        and a["zip"] == b["zip"]
    )


async def find_customer_by_email(db: SupabaseAdmin, email: str) -> dict[str, Any] | None:
    data = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "customer_id,email,first_name,last_name,full_name,designation,company_name,company_domain,email_verified,email_verified_at,last_login_at,metadata,stripe_customer_id,billing_period_start,billing_period_end,payment_method_id",
            "email": f"eq.{email}",
            "limit": 1,
        },
    )
    return data[0] if data else None


async def find_customer_by_id(db: SupabaseAdmin, customer_id: str | None) -> dict[str, Any] | None:
    if not customer_id:
        return None
    data = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "customer_id,email,first_name,last_name,full_name,designation,company_name,company_domain,email_verified,email_verified_at,last_login_at,metadata,stripe_customer_id,billing_period_start,billing_period_end,payment_method_id",
            "customer_id": f"eq.{customer_id}",
            "limit": 1,
        },
    )
    return data[0] if data else None


async def resolve_customer_for_billing(db: SupabaseAdmin, body: dict[str, Any]) -> dict[str, Any]:
    raw_customer_id = str(body.get("customer_id") or body.get("customerId") or "").strip()
    email = (body.get("email") or "").strip().lower()

    customer_by_id = await find_customer_by_id(db, raw_customer_id) if raw_customer_id else None
    customer_by_email = await find_customer_by_email(db, email) if email else None

    if raw_customer_id and not customer_by_id:
        raise HTTPException(status_code=404, detail="Customer not found for provided customer_id.")

    if email and not customer_by_email and not customer_by_id:
        raise HTTPException(status_code=404, detail="Customer not found.")

    if customer_by_id and customer_by_email and customer_by_id["customer_id"] != customer_by_email["customer_id"]:
        raise HTTPException(
            status_code=409,
            detail="Customer identity mismatch. Please sign out and sign in again.",
        )

    customer = customer_by_id or customer_by_email
    if not customer:
        raise HTTPException(status_code=422, detail="Either customer_id or email is required.")
    return customer


def _extract_authenticated_email(user: Any) -> str:
    if isinstance(user, dict):
        return normalize_email(user.get("email"))
    return normalize_email(getattr(user, "email", None))


async def get_authenticated_customer(
    db: SupabaseAdmin,
    request: Request,
    *,
    expected_email: str | None = None,
) -> dict[str, Any]:
    access_token = clean_optional(request.cookies.get("access_token"))
    if not access_token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        auth_client = get_auth_client()
        try:
            auth_response = auth_client.auth.get_user(access_token)
        except TypeError:
            auth_response = auth_client.auth.get_user(jwt=access_token)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Session expired or invalid.") from exc

    auth_user = getattr(auth_response, "user", None)
    if auth_user is None and isinstance(auth_response, dict):
        auth_user = auth_response.get("user")

    authenticated_email = _extract_authenticated_email(auth_user)
    if not authenticated_email:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if expected_email and normalize_email(expected_email) and normalize_email(expected_email) != authenticated_email:
        raise HTTPException(status_code=403, detail="Authenticated user does not match the request.")

    customer = await find_customer_by_email(db, authenticated_email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return customer


async def find_account_by_id(db: SupabaseAdmin, account_id: str | None) -> dict[str, Any] | None:
    if not account_id:
        return None
    data = await db.request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "account_id,company_name,account_domain",
            "account_id": f"eq.{account_id}",
            "limit": 1,
        },
    )
    return data[0] if data else None


def is_missing_relation_error(exc: HTTPException, relation_name: str) -> bool:
    source = str(exc.detail or "").lower()
    return "42p01" in source or relation_name.lower() in source


def build_customer_metadata(existing_metadata: Any, is_verified: bool) -> dict[str, Any]:
    source = existing_metadata if isinstance(existing_metadata, dict) else {}
    next_metadata = {**source, "source": "Automatisor_signup"}
    if is_verified:
        next_metadata["email_verified_at"] = datetime.now(timezone.utc).isoformat()
    return next_metadata


async def upsert_customer(db: SupabaseAdmin, data: dict[str, Any]) -> dict[str, Any]:
    customer = await find_customer_by_email(db, data["email"])
    full_name = " ".join(part for part in [data.get("firstName"), data.get("lastName")] if part).strip()
    payload = {
        "first_name": data.get("firstName") or None,
        "last_name": data.get("lastName") or None,
        "full_name": full_name or None,
        "designation": data.get("designation") or None,
        "company_name": data.get("companyName") or None,
        "company_domain": data.get("companyDomain") or None,
        "email_verified": bool(data.get("isVerified")),
        "email_verified_at": datetime.now(timezone.utc).isoformat() if data.get("isVerified") else None,
        "last_login_at": datetime.now(timezone.utc).isoformat() if data.get("touchLogin") else None,
        "metadata": build_customer_metadata(customer.get("metadata") if customer else None, bool(data.get("isVerified"))),
    }
    if customer:
        patch_payload = {key: value for key, value in payload.items() if value is not None or key in {"email_verified"}}
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer",
            params={"customer_id": f"eq.{customer['customer_id']}"},
            json_body=patch_payload,
            headers={"Prefer": "return=minimal"},
        )
        return {"customerId": customer["customer_id"], "email": customer["email"]}

    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer",
        params={"select": "customer_id,email"},
        json_body={"email": data["email"], **payload},
        headers={"Prefer": "return=representation"},
    )
    row = created[0]
    return {"customerId": row["customer_id"], "email": row["email"]}


async def create_stripe_customer(db: SupabaseAdmin, customer_id: str, email: str, full_name: str | None) -> str:
    """
    Creates a Stripe Customer and writes stripe_customer_id
    back to automatisor_customer. Idempotent — if stripe_customer_id already
    exists, returns it without calling Stripe again.
    """
    existing = await db.request(
        "GET",
        "/rest/v1/automatisor_customer",
        params={
            "select": "stripe_customer_id",
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

    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer_id}"},
        json_body={
            "stripe_customer_id": stripe_customer.id,
        },
        headers={"Prefer": "return=minimal"},
    )
    return stripe_customer.id


async def mark_customer_verified(db: SupabaseAdmin, email: str) -> str | None:
    customer = await find_customer_by_email(db, email)
    if not customer:
        return None
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer['customer_id']}"},
        json_body={
            "email_verified": True,
            "email_verified_at": datetime.now(timezone.utc).isoformat(),
            "metadata": build_customer_metadata(customer.get("metadata"), True),
        },
        headers={"Prefer": "return=minimal"},
    )
    return customer["customer_id"]


async def touch_customer_login(db: SupabaseAdmin, customer_id: str | None) -> None:
    if not customer_id:
        return
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer_id}"},
        json_body={"last_login_at": datetime.now(timezone.utc).isoformat()},
        headers={"Prefer": "return=minimal"},
    )


def build_customer_site_metadata(
    existing_metadata: Any,
    *,
    requested_at: str | None = None,
    generated_at: str | None = None,
    address_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = existing_metadata if isinstance(existing_metadata, dict) else {}
    next_metadata = {**source}
    if requested_at:
        next_metadata["last_pre_assessment_requested_at"] = requested_at
    if generated_at:
        next_metadata["last_pre_assessment_generated_at"] = generated_at
    if address_validation:
        next_metadata["address_validation"] = address_validation
    return next_metadata


async def count_customer_sites_by_account(db: SupabaseAdmin, account_ids: list[str], customer_id: str | None) -> dict[str, int]:
    ids = [value for value in account_ids if value]
    if not ids:
        return {}
    data = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "account_id",
            "customer_id": f"eq.{customer_id}",
            "account_id": f"in.({','.join(ids)})",
        },
    )
    counts: dict[str, int] = {}
    for row in data or []:
        key = row.get("account_id")
        counts[key] = counts.get(key, 0) + 1
    return counts


def choose_active_account(accounts: list[dict[str, Any]], requested_account_id: str | None) -> dict[str, Any] | None:
    if not accounts:
        return None
    if requested_account_id:
        for row in accounts:
            if row.get("account_id") == requested_account_id:
                return row
    return accounts[0]


async def list_customer_accounts(db: SupabaseAdmin, customer: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not customer or not customer.get("customer_id"):
        return []
    assignment_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "account_id,created_at",
            "customer_id": f"eq.{customer['customer_id']}",
            "order": "created_at.desc",
        },
    )
    account_ids = []
    for row in assignment_rows or []:
        account_id = row.get("account_id")
        if account_id and account_id not in account_ids:
            account_ids.append(account_id)
    if not account_ids:
        return []
    account_rows = await db.request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "account_id,company_name,account_domain",
            "account_id": f"in.({','.join(account_ids)})",
        },
    )
    by_id = {row["account_id"]: row for row in account_rows or []}
    accounts = []
    for account_id in account_ids:
        row = by_id.get(account_id)
        if not row:
            continue
        accounts.append(
            {
                "account_id": row["account_id"],
                "company_name": row.get("company_name") or "",
                "company_domain": row.get("account_domain") or "",
            }
        )
    return accounts


async def find_account_for_customer_site(db: SupabaseAdmin, customer_id: str | None, account_id: str) -> dict[str, str] | None:
    if not customer_id or not account_id:
        return None
    data = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "account_id",
            "customer_id": f"eq.{customer_id}",
            "account_id": f"eq.{account_id}",
            "limit": 1,
        },
    )
    if not data:
        return None
    account = await find_account_by_id(db, account_id)
    if not account:
        return None
    return {
        "accountId": account["account_id"],
        "companyName": account.get("company_name") or "",
        "domain": account.get("account_domain") or "",
    }


async def hydrate_recommendations_by_assignment(
    db: SupabaseAdmin,
    assignment_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    refs_by_assignment: dict[str, dict[str, Any]] = {}
    site_ids: list[str] = []
    for assignment in assignment_rows:
        customer_site_id = assignment.get("customer_site_id")
        if not customer_site_id:
            continue
        recommendations = assignment.get("recommendations") or {}
        if not isinstance(recommendations, dict):
            recommendations = {}
        refs_by_assignment[customer_site_id] = recommendations
        for branch in ["company_sites", "nearby_sites"]:
            for item in recommendations.get(branch) or []:
                if not isinstance(item, dict):
                    continue
                site_id = clean_optional(item.get("site_id"))
                if site_id and site_id not in site_ids:
                    site_ids.append(site_id)
    if not refs_by_assignment:
        return result
    site_map = await fetch_recommendation_site_map(db, site_ids)
    for customer_site_id, recommendations in refs_by_assignment.items():
        result[customer_site_id] = hydrate_recommendations_from_site_map(recommendations, site_map)
    return result


async def hydrate_recommendations(db: SupabaseAdmin, recommendations: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(recommendations, dict):
        return {}
    site_ids: list[str] = []
    for branch in ["company_sites", "nearby_sites"]:
        for item in recommendations.get(branch) or []:
            if not isinstance(item, dict):
                continue
            site_id = clean_optional(item.get("site_id"))
            if site_id and site_id not in site_ids:
                site_ids.append(site_id)
    site_map = await fetch_recommendation_site_map(db, site_ids)
    return hydrate_recommendations_from_site_map(recommendations, site_map)


async def fetch_recommendation_site_map(db: SupabaseAdmin, site_ids: list[str]) -> dict[str, dict[str, Any]]:
    ids = [site_id for site_id in site_ids if site_id]
    if not ids:
        return {}
    site_rows = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,metadata",
            "site_id": f"in.({','.join(ids)})",
            "is_archived": "eq.false",
        },
    )
    account_ids: list[str] = []
    for row in site_rows or []:
        account_id = clean_optional(row.get("account_id"))
        if account_id and account_id not in account_ids:
            account_ids.append(account_id)
    account_map: dict[str, dict[str, Any]] = {}
    if account_ids:
        account_rows = await db.request(
            "GET",
            "/rest/v1/accounts",
            params={
                "select": "account_id,company_name,account_domain",
                "account_id": f"in.({','.join(account_ids)})",
            },
        )
        account_map = {row["account_id"]: row for row in account_rows or [] if row.get("account_id")}
    return {
        row["site_id"]: hydrate_recommendation_site_row(row, account_map.get(row.get("account_id")) or {})
        for row in site_rows or []
        if row.get("site_id")
    }


def hydrate_recommendations_from_site_map(
    recommendations: dict[str, Any],
    site_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    hydrated = {
        "status": clean_optional(recommendations.get("status")),
        "company_sites": hydrate_recommendation_branch(
            recommendations.get("company_sites"),
            site_map,
            "recommendation_site_discovery",
        ),
        "nearby_sites": hydrate_recommendation_branch(
            recommendations.get("nearby_sites"),
            site_map,
            "recommendation_nearby_site",
        ),
    }
    errors = recommendations.get("errors")
    if isinstance(errors, dict) and errors:
        hydrated["errors"] = errors
    return hydrated


def hydrate_recommendation_branch(
    items: Any,
    site_map: dict[str, dict[str, Any]],
    metadata_key: str,
) -> list[dict[str, Any]]:
    hydrated: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        site_id = clean_optional(item.get("site_id"))
        if not site_id:
            continue
        site = site_map.get(site_id)
        if not site:
            hydrated.append({"site_id": site_id})
            continue
        metadata = site.get("metadata") if isinstance(site.get("metadata"), dict) else {}
        recommendation_metadata = metadata.get(metadata_key) if isinstance(metadata.get(metadata_key), dict) else {}
        hydrated.append(
            {
                **item,
                "site_id": site_id,
                "account_id": site.get("account_id") or "",
                "company_name": recommendation_metadata.get("company_name") or site.get("company_name") or "",
                "company_domain": site.get("company_domain") or "",
                "website": site.get("company_domain") or "",
                "site_address": recommendation_metadata.get("site_address") or site.get("full_address") or "",
                "full_address": site.get("full_address") or "",
                "google_maps_uri": recommendation_metadata.get("google_maps_uri") or "",
            }
        )
    return hydrated


def hydrate_recommendation_site_row(row: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "company_name": row.get("company_name") or account.get("company_name") or "",
        "company_domain": account.get("account_domain") or "",
    }


_ASSIGNMENT_SELECT = (
    "customer_site_id,assigned_via,metadata,recommendations,notes,report_metadata,rating_metadata,is_report_ready,account_id"
)


async def _hydrate_account_site_with_assignment(
    db: SupabaseAdmin,
    assignment: dict[str, Any] | None,
    *,
    account_id: str | None,
    site_id: str,
) -> dict[str, Any] | None:
    resolved_account_id = account_id or (assignment.get("account_id") if assignment else None)
    if not resolved_account_id:
        return None
    data = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,metadata,created_at",
            "account_id": f"eq.{resolved_account_id}",
            "site_id": f"eq.{site_id}",
            "is_archived": "eq.false",
            "limit": 1,
        },
    )
    row = data[0] if data else None
    if not row:
        return None
    if assignment:
        row["customer_site_id"] = assignment.get("customer_site_id")
        row["assigned_via"] = assignment.get("assigned_via") or "user_added_site"
        row["customer_site_metadata"] = assignment.get("metadata") or {}
        row["recommendations"] = await hydrate_recommendations(db, assignment.get("recommendations") or {})
        row["notes"] = assignment.get("notes") or ""
        row["report_metadata"] = assignment.get("report_metadata") or {}
        row["rating_metadata"] = assignment.get("rating_metadata") or {}
        row["is_report_ready"] = bool(assignment.get("is_report_ready"))
    return row


async def find_account_site_by_id(
    db: SupabaseAdmin,
    account_id: str | None,
    site_id: str | None,
    customer_id: str | None,
    *,
    customer_site_id: str | None = None,
) -> dict[str, Any] | None:
    if not site_id:
        return None

    assignment: dict[str, Any] | None = None
    if customer_site_id and customer_id:
        assignment = await find_customer_site_assignment(db, customer_id, customer_site_id)
        if assignment and assignment.get("site_id") != site_id:
            assignment = None

    if not assignment and customer_id:
        base_params: dict[str, str] = {
            "select": _ASSIGNMENT_SELECT,
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "limit": "1",
        }
        if account_id:
            base_params["account_id"] = f"eq.{account_id}"

        owned_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={**base_params, "assigned_via": "in.(user_added_site,dev_added_site)"},
        )
        assignment = owned_rows[0] if owned_rows else None

        if not assignment:
            shared_rows = await db.request(
                "GET",
                "/rest/v1/automatisor_customer_sites",
                params={**base_params, "assigned_via": "eq.shared_site"},
            )
            assignment = shared_rows[0] if shared_rows else None

    if customer_id and not assignment and not account_id:
        return None

    return await _hydrate_account_site_with_assignment(
        db,
        assignment,
        account_id=account_id,
        site_id=site_id,
    )


async def list_customer_sites(db: SupabaseAdmin, customer_id: str | None) -> list[dict[str, Any]]:
    if not customer_id:
        return []
    assignment_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,site_id,account_id,assigned_via,metadata,recommendations,notes,report_metadata,rating_metadata,is_report_ready,created_at,shared_by",
            "customer_id": f"eq.{customer_id}",
            "assigned_via": "neq.sample_site",
            "order": "created_at.desc",
        },
    )
    if not assignment_rows:
        return []
    
    # Get unique site_ids for fetching site details
    unique_site_ids = list(set(row["site_id"] for row in assignment_rows if row.get("site_id")))
    if not unique_site_ids:
        return []
    
    # Fetch site details for all unique sites
    site_details = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,created_at,metadata",
            "site_id": f"in.({','.join(unique_site_ids)})",
            "is_archived": "eq.false",
        },
    )
    
    # Create lookup dict for site details
    site_details_by_id = {row["site_id"]: row for row in site_details or [] if row.get("site_id")}
    
    # Hydrate recommendations for all assignments
    hydrated_recommendations = await hydrate_recommendations_by_assignment(db, assignment_rows)
    
    # Build result list with ALL assignments (including duplicate shared sites)
    sites = []
    for assignment in assignment_rows:
        site_id = assignment.get("site_id")
        site_detail = site_details_by_id.get(site_id)
        if not site_detail:
            continue
        
        sites.append(
            {
                **site_detail,
                "customer_site_id": assignment.get("customer_site_id"),
                "assigned_via": assignment.get("assigned_via") or "user_added_site",
                "shared_by": assignment.get("shared_by"),
                "added_at": assignment.get("created_at"),
                "customer_site_metadata": assignment.get("metadata") or {},
                "recommendations": hydrated_recommendations.get(assignment.get("customer_site_id")) or {},
                "notes": assignment.get("notes") or "",
                "report_metadata": assignment.get("report_metadata") or {},
                "rating_metadata": assignment.get("rating_metadata") or {},
                "is_report_ready": bool(assignment.get("is_report_ready")),
            }
        )
    return sites


async def list_customer_wishlist(db: SupabaseAdmin, customer_id: str | None) -> list[dict[str, Any]]:
    if not customer_id:
        return []
    context_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": CUSTOMER_CONTEXT_SELECT,
            "customer_id": f"eq.{customer_id}",
            "event_type": "eq.wish_list",
            "order": "created_at.desc",
        },
    )
    site_ids: list[str] = []
    for row in context_rows or []:
        site_id = clean_optional(row.get("site_id"))
        if site_id and site_id not in site_ids:
            site_ids.append(site_id)
    if not site_ids:
        return []
    site_rows = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,metadata,created_at",
            "site_id": f"in.({','.join(site_ids)})",
            "is_archived": "eq.false",
        },
    )
    site_by_id = {row["site_id"]: row for row in site_rows or [] if row.get("site_id")}
    account_ids: list[str] = []
    for site in site_rows or []:
        account_id = clean_optional(site.get("account_id"))
        if account_id and account_id not in account_ids:
            account_ids.append(account_id)
    account_map: dict[str, dict[str, Any]] = {}
    if account_ids:
        account_rows = await db.request(
            "GET",
            "/rest/v1/accounts",
            params={
                "select": "account_id,company_name,account_domain",
                "account_id": f"in.({','.join(account_ids)})",
            },
        )
        account_map = {row["account_id"]: row for row in account_rows or [] if row.get("account_id")}
    wishlist = []
    for row in context_rows or []:
        site = site_by_id.get(row.get("site_id"))
        if not site:
            continue
        account = account_map.get(site.get("account_id")) or {}
        wishlist.append(
            {
                **row,
                "notes": row.get("notes") or "",
                "site_id": site.get("site_id"),
                "account_id": site.get("account_id") or row.get("account_id"),
                "company_name": site.get("company_name") or account.get("company_name") or "",
                "company_domain": account.get("account_domain") or "",
                "full_address": site.get("full_address") or "",
                "site_metadata": site.get("metadata") or {},
                "added_at": row.get("created_at"),
            }
        )
    requested_site_ids = await site_ids_with_pre_assessment_requested(db, customer_id, site_ids)
    if requested_site_ids:
        for requested_site_id in requested_site_ids:
            try:
                await remove_customer_wishlist_item(db, customer_id, requested_site_id)
            except Exception as exc:
                print(f"Stale wishlist cleanup failed for site {requested_site_id}: {exc}")
        wishlist = [item for item in wishlist if item.get("site_id") not in requested_site_ids]
    return wishlist


async def site_ids_with_pre_assessment_requested(
    db: SupabaseAdmin,
    customer_id: str,
    site_ids: list[str],
) -> set[str]:
    if not customer_id or not site_ids:
        return set()
    requested: set[str] = set()
    assignment_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "site_id,metadata",
            "customer_id": f"eq.{customer_id}",
            "site_id": f"in.({','.join(site_ids)})",
        },
    )
    for row in assignment_rows or []:
        site_id = clean_optional(row.get("site_id"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if site_id and metadata.get("last_pre_assessment_requested_at"):
            requested.add(site_id)
    remaining = [site_id for site_id in site_ids if site_id not in requested]
    if remaining:
        billing_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_billing",
            params={
                "select": "site_id",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"in.({','.join(remaining)})",
                "usage_type": "eq.pre_assessment_request",
            },
        )
        for row in billing_rows or []:
            site_id = clean_optional(row.get("site_id"))
            if site_id:
                requested.add(site_id)
    return requested


def _hydrate_wishlist_item_row(
    row: dict[str, Any],
    site: dict[str, Any],
    account: dict[str, Any],
) -> dict[str, Any]:
    return {
        **row,
        "notes": row.get("notes") or "",
        "site_id": site.get("site_id"),
        "account_id": site.get("account_id") or row.get("account_id"),
        "company_name": site.get("company_name") or account.get("company_name") or "",
        "company_domain": account.get("account_domain") or "",
        "full_address": site.get("full_address") or "",
        "site_metadata": site.get("metadata") or {},
        "added_at": row.get("created_at"),
    }


async def get_wishlist_notes_for_site(db: SupabaseAdmin, customer_id: str, site_id: str) -> str:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": "notes",
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "event_type": f"eq.{EVENT_TYPE_WISH_LIST}",
            "limit": 1,
        },
    )
    row = rows[0] if rows else None
    return str(row.get("notes") or "") if row else ""


async def apply_wishlist_notes_to_customer_site(
    db: SupabaseAdmin,
    customer_site_id: str | None,
    wishlist_notes: str,
) -> None:
    notes = str(wishlist_notes or "").strip()
    customer_site_id = clean_optional(customer_site_id)
    if not notes or not customer_site_id:
        return
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,notes",
            "customer_site_id": f"eq.{customer_site_id}",
            "limit": 1,
        },
    )
    row = rows[0] if rows else None
    if not row or str(row.get("notes") or "").strip():
        return
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer_sites",
        params={"customer_site_id": f"eq.{customer_site_id}"},
        json_body={
            "notes": notes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Prefer": "return=minimal"},
    )


async def resolve_wishlist_notes_from_discovery(
    db: SupabaseAdmin,
    customer_id: str,
    site_id: str,
    metadata: dict[str, Any] | None,
    explicit_notes: str | None = None,
) -> str:
    notes = str(explicit_notes or "").strip()
    if notes:
        return notes
    if not isinstance(metadata, dict):
        return ""
    source_company_context_id = clean_optional(metadata.get("source_company_context_id"))
    if not source_company_context_id:
        return ""
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": "metadata",
            "customer_context_id": f"eq.{source_company_context_id}",
            "customer_id": f"eq.{customer_id}",
            "event_type": f"eq.{EVENT_TYPE_USER_ADDED_COMPANY}",
            "limit": 1,
        },
    )
    company_row = rows[0] if rows else None
    if not company_row:
        return ""
    company_metadata = company_row.get("metadata") if isinstance(company_row.get("metadata"), dict) else {}
    return discovery_site_note_from_company_metadata(company_metadata, site_id)


def discovery_site_note_from_company_metadata(metadata: Any, site_id: str) -> str:
    if not isinstance(metadata, dict):
        return ""
    discovery = metadata.get("discovery")
    if not isinstance(discovery, dict):
        return ""
    for item in discovery.get("company_sites") or []:
        if not isinstance(item, dict):
            continue
        if clean_optional(item.get("site_id")) == site_id:
            return str(item.get("note") or "")
    return ""


async def add_customer_wishlist_item(
    db: SupabaseAdmin,
    customer_id: str,
    account_id: str,
    site_id: str,
    metadata: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    site_rows = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,metadata",
            "account_id": f"eq.{account_id}",
            "site_id": f"eq.{site_id}",
            "is_archived": "eq.false",
            "limit": 1,
        },
    )
    site = site_rows[0] if site_rows else None
    if not site:
        raise HTTPException(status_code=422, detail="Selected wishlist site was not found")
    account_rows = await db.request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "account_id,company_name,account_domain",
            "account_id": f"eq.{account_id}",
            "limit": 1,
        },
    )
    account = account_rows[0] if account_rows else {}
    existing_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": CUSTOMER_CONTEXT_SELECT,
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "event_type": "eq.wish_list",
            "limit": 1,
        },
    )
    now = datetime.now(timezone.utc).isoformat()
    row = existing_rows[0] if existing_rows else None
    resolved_notes = str(notes or "").strip()
    if row:
        existing_notes = str(row.get("notes") or "").strip()
        if resolved_notes and not existing_notes:
            await db.request(
                "PATCH",
                "/rest/v1/automatisor_customer_context",
                params={"customer_context_id": f"eq.{row['customer_context_id']}"},
                json_body={
                    "notes": resolved_notes,
                    "updated_at": now,
                },
                headers={"Prefer": "return=minimal"},
            )
            row = {**row, "notes": resolved_notes}
        return _hydrate_wishlist_item_row(row, site, account)
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_context",
        params={"select": CUSTOMER_CONTEXT_SELECT},
        json_body={
            "customer_id": customer_id,
            "site_id": site_id,
            "account_id": account_id,
            "event_type": "wish_list",
            "metadata": metadata or {},
            "notes": resolved_notes,
            "created_at": now,
            "updated_at": now,
        },
        headers={"Prefer": "return=representation"},
    )
    result = created[0]
    return _hydrate_wishlist_item_row(result, site, account)


async def remove_customer_wishlist_item(
    db: SupabaseAdmin,
    customer_id: str,
    site_id: str,
) -> bool:
    customer_id = clean_optional(customer_id)
    site_id = clean_optional(site_id)
    if not customer_id or not site_id:
        return False
    await db.request(
        "DELETE",
        "/rest/v1/automatisor_customer_context",
        params={
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "event_type": f"eq.{EVENT_TYPE_WISH_LIST}",
        },
        headers={"Prefer": "return=minimal"},
    )
    return True


async def clear_wishlist_after_pre_assessment(
    db: SupabaseAdmin,
    customer_id: str,
    site_id: str,
    *,
    request: Request | None = None,
    body: dict[str, Any] | None = None,
) -> None:
    if request is not None and body is not None and is_dry_run_request(request, body):
        return
    try:
        await remove_customer_wishlist_item(db, customer_id, site_id)
    except Exception as exc:
        print(f"Wishlist removal after pre-assessment failed: {exc}")


def default_company_discovery_metadata() -> dict[str, Any]:
    return {"discovery": {"status": "idle", "company_sites": [], "errors": []}}


def discovery_from_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return default_company_discovery_metadata()["discovery"]
    discovery = metadata.get("discovery")
    if not isinstance(discovery, dict):
        return default_company_discovery_metadata()["discovery"]
    return {
        "status": clean_optional(discovery.get("status")) or "idle",
        "requested_at": clean_optional(discovery.get("requested_at")) or None,
        "ready_at": clean_optional(discovery.get("ready_at")) or None,
        "company_sites": discovery.get("company_sites") if isinstance(discovery.get("company_sites"), list) else [],
        "errors": discovery.get("errors") if isinstance(discovery.get("errors"), list) else [],
    }


async def hydrate_company_discovery(db: SupabaseAdmin, metadata: Any) -> dict[str, Any]:
    discovery = discovery_from_metadata(metadata)
    payload = {
        "status": discovery["status"],
        "requested_at": discovery.get("requested_at"),
        "ready_at": discovery.get("ready_at"),
        "company_sites": discovery.get("company_sites") or [],
    }
    hydrated = hydrate_recommendations_from_site_map(payload, await fetch_recommendation_site_map(db, _discovery_site_ids(discovery)))
    if discovery.get("requested_at"):
        hydrated["requested_at"] = discovery["requested_at"]
    if discovery.get("ready_at"):
        hydrated["ready_at"] = discovery["ready_at"]
    errors = discovery.get("errors")
    if errors:
        hydrated["errors"] = errors
    return hydrated


def _discovery_site_ids(discovery: dict[str, Any]) -> list[str]:
    site_ids: list[str] = []
    for item in discovery.get("company_sites") or []:
        if not isinstance(item, dict):
            continue
        site_id = clean_optional(item.get("site_id"))
        if site_id and site_id not in site_ids:
            site_ids.append(site_id)
    return site_ids


async def _hydrate_customer_company_row(db: SupabaseAdmin, row: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    discovery = await hydrate_company_discovery(db, metadata)
    return {
        **row,
        "notes": row.get("notes") or "",
        "company_name": account.get("company_name") or "",
        "company_domain": account.get("account_domain") or "",
        "discovery": discovery,
    }


async def list_customer_companies(db: SupabaseAdmin, customer_id: str | None) -> list[dict[str, Any]]:
    if not customer_id:
        return []
    context_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": CUSTOMER_CONTEXT_SELECT,
            "customer_id": f"eq.{customer_id}",
            "event_type": f"eq.{EVENT_TYPE_USER_ADDED_COMPANY}",
            "order": "created_at.desc",
        },
    )
    account_ids: list[str] = []
    for row in context_rows or []:
        account_id = clean_optional(row.get("account_id"))
        if account_id and account_id not in account_ids:
            account_ids.append(account_id)
    account_map: dict[str, dict[str, Any]] = {}
    if account_ids:
        account_rows = await db.request(
            "GET",
            "/rest/v1/accounts",
            params={
                "select": "account_id,company_name,account_domain",
                "account_id": f"in.({','.join(account_ids)})",
            },
        )
        account_map = {row["account_id"]: row for row in account_rows or [] if row.get("account_id")}
    companies: list[dict[str, Any]] = []
    for row in context_rows or []:
        account = account_map.get(row.get("account_id")) or {}
        companies.append(await _hydrate_customer_company_row(db, row, account))
    return companies


async def find_customer_company_by_id(
    db: SupabaseAdmin,
    customer_id: str,
    customer_context_id: str,
) -> dict[str, Any] | None:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": CUSTOMER_CONTEXT_SELECT,
            "customer_context_id": f"eq.{customer_context_id}",
            "customer_id": f"eq.{customer_id}",
            "event_type": f"eq.{EVENT_TYPE_USER_ADDED_COMPANY}",
            "limit": 1,
        },
    )
    row = rows[0] if rows else None
    if not row:
        return None
    account_rows = await db.request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "account_id,company_name,account_domain",
            "account_id": f"eq.{row.get('account_id')}",
            "limit": 1,
        },
    )
    account = account_rows[0] if account_rows else {}
    return await _hydrate_customer_company_row(db, row, account)


async def save_customer_company(
    db: SupabaseAdmin,
    customer_id: str,
    org_name: str,
    domain: str,
) -> dict[str, Any]:
    account = await upsert_account(db, org_name, domain)
    account_id = account["accountId"]
    existing_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": CUSTOMER_CONTEXT_SELECT,
            "customer_id": f"eq.{customer_id}",
            "account_id": f"eq.{account_id}",
            "event_type": f"eq.{EVENT_TYPE_USER_ADDED_COMPANY}",
            "limit": 1,
        },
    )
    if existing_rows:
        account_rows = await db.request(
            "GET",
            "/rest/v1/accounts",
            params={
                "select": "account_id,company_name,account_domain",
                "account_id": f"eq.{account_id}",
                "limit": 1,
            },
        )
        account_row = account_rows[0] if account_rows else {}
        return await _hydrate_customer_company_row(db, existing_rows[0], account_row)
    now = datetime.now(timezone.utc).isoformat()
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_context",
        params={"select": CUSTOMER_CONTEXT_SELECT},
        json_body={
            "customer_id": customer_id,
            "account_id": account_id,
            "site_id": None,
            "event_type": EVENT_TYPE_USER_ADDED_COMPANY,
            "metadata": default_company_discovery_metadata(),
            "created_at": now,
            "updated_at": now,
        },
        headers={"Prefer": "return=representation"},
    )
    account_rows = await db.request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "account_id,company_name,account_domain",
            "account_id": f"eq.{account_id}",
            "limit": 1,
        },
    )
    account_row = account_rows[0] if account_rows else {"company_name": org_name, "account_domain": domain}
    return await _hydrate_customer_company_row(db, created[0], account_row)


async def trigger_company_discovery(
    db: SupabaseAdmin,
    customer_id: str,
    customer_context_id: str,
) -> dict[str, Any]:
    company = await find_customer_company_by_id(db, customer_id, customer_context_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found.")
    discovery = company.get("discovery") or {}
    status = clean_optional(discovery.get("status")) or "idle"
    if status == "running":
        raise HTTPException(status_code=422, detail="Facility discovery is already running for this company.")
    if status == "review":
        raise HTTPException(status_code=422, detail="Facility discovery is awaiting review for this company.")
    if status == "ready":
        raise HTTPException(status_code=422, detail="Facilities are already available for this company.")
    requested_at = datetime.now(timezone.utc).isoformat()
    metadata = company.get("metadata") if isinstance(company.get("metadata"), dict) else default_company_discovery_metadata()
    metadata = {
        **metadata,
        "discovery": {
            **discovery_from_metadata(metadata),
            "status": "running",
            "requested_at": requested_at,
            "ready_at": None,
            "errors": [],
        },
    }
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer_context",
        params={"customer_context_id": f"eq.{customer_context_id}"},
        json_body={"metadata": metadata, "updated_at": requested_at},
        headers={"Prefer": "return=minimal"},
    )
    updated = await find_customer_company_by_id(db, customer_id, customer_context_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Company not found.")
    return updated


async def _enqueue_company_discovery_on_worker(
    customer_context_ids: list[str],
    *,
    send_email: bool = True,
) -> bool:
    if not RECOMMENDATION_SYSTEM_URL:
        print("RECOMMENDATION_SYSTEM_URL not set; skipping company discovery enqueue")
        return False
    if not RECOMMENDATION_WORKER_SECRET:
        print("RECOMMENDATION_WORKER_SECRET not set; skipping company discovery enqueue")
        return False
    ids = [clean_optional(item) for item in customer_context_ids]
    ids = [item for item in ids if item]
    if not ids:
        return False

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RECOMMENDATION_WORKER_SECRET}",
    }

    if len(ids) == 1:
        url = f"{RECOMMENDATION_SYSTEM_URL}/recommendations/company-discovery"
        payload = {
            "customer_context_id": ids[0],
            "send_email": send_email,
        }
    else:
        url = f"{RECOMMENDATION_SYSTEM_URL}/recommendations/company-discovery/bulk"
        payload = {
            "customer_context_ids": ids,
            "send_email": send_email,
        }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.is_error:
            print(
                f"Company discovery enqueue failed ({response.status_code}): {response.text}"
            )
            return False
        print(
            f"Company discovery enqueued for {len(ids)} company context(s): "
            f"{response.status_code} {response.text[:200]}",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"Company discovery enqueue failed: {exc}")
        return False


SITE_RECOMMENDATION_DEFAULT_LIMIT = 5


async def _enqueue_site_recommendations_on_worker(
    customer_site_id: str,
    *,
    limit: int = SITE_RECOMMENDATION_DEFAULT_LIMIT,
) -> bool:
    if not RECOMMENDATION_SYSTEM_URL:
        print("RECOMMENDATION_SYSTEM_URL not set; skipping site recommendations enqueue")
        return False
    if not RECOMMENDATION_WORKER_SECRET:
        print("RECOMMENDATION_WORKER_SECRET not set; skipping site recommendations enqueue")
        return False
    customer_site_id = clean_optional(customer_site_id)
    if not customer_site_id:
        return False

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RECOMMENDATION_WORKER_SECRET}",
    }

    url = f"{RECOMMENDATION_SYSTEM_URL}/recommendations/site-recommendations"
    payload = {
        "customer_site_id": customer_site_id,
        "limit": limit,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.is_error:
            print(
                f"Site recommendations enqueue failed ({response.status_code}): {response.text}"
            )
            return False
        print(
            f"Site recommendations worker accepted enqueue for customer_site_id={customer_site_id}: "
            f"{response.status_code} {response.text[:200]}",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"Site recommendations enqueue failed: {exc}")
        return False


async def maybe_start_site_recommendations(
    db: SupabaseAdmin,
    site: dict[str, Any],
    *,
    assignment_customer_site_id: str | None = None,
) -> None:
    customer_site_id = clean_optional(assignment_customer_site_id) or clean_optional(
        site.get("customer_site_id")
    )
    if not customer_site_id:
        print("Site recommendations skip: missing customer_site_id")
        return

    fresh_recommendations = await fetch_assignment_recommendations(db, customer_site_id)
    current_status = site_recommendations_status({"recommendations": fresh_recommendations})
    if current_status in ("running", "review", "ready"):
        print(
            f"Site recommendations skip: customer_site_id={customer_site_id} "
            f"status={current_status or 'idle'}"
        )
        return

    enqueued = await _enqueue_site_recommendations_on_worker(customer_site_id)
    if not enqueued:
        print(
            f"Site recommendations enqueue failed for customer_site_id={customer_site_id}",
            flush=True,
        )
        return

    await mark_site_recommendations_running(db, customer_site_id)
    print(f"Site recommendations started: customer_site_id={customer_site_id}")


def site_recommendations_status(site: dict[str, Any]) -> str:
    recommendations = site.get("recommendations") or {}
    if not isinstance(recommendations, dict):
        return ""
    return clean_optional(recommendations.get("status")) or ""


def should_enqueue_site_recommendations(site: dict[str, Any]) -> bool:
    return site_recommendations_status(site) not in ("running", "review", "ready")


async def fetch_assignment_recommendations(
    db: SupabaseAdmin,
    customer_site_id: str,
) -> dict[str, Any]:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "recommendations",
            "customer_site_id": f"eq.{customer_site_id}",
            "limit": 1,
        },
    )
    if not rows:
        return {}
    recommendations = rows[0].get("recommendations") or {}
    return recommendations if isinstance(recommendations, dict) else {}


async def mark_site_recommendations_running(db: SupabaseAdmin, customer_site_id: str) -> None:
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer_sites",
        params={"customer_site_id": f"eq.{customer_site_id}"},
        json_body={
            "recommendations": {
                "status": "running",
                "company_sites": [],
                "nearby_sites": [],
            },
        },
        headers={"Prefer": "return=minimal"},
    )


async def get_customer_user_context(
    db: SupabaseAdmin,
    customer_id: str,
) -> dict[str, Any] | None:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_context",
        params={
            "select": "customer_context_id,customer_id,site_id,account_id,event_type,metadata,created_at,updated_at",
            "customer_id": f"eq.{customer_id}",
            "event_type": "eq.user_context",
            "order": "updated_at.desc",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def upsert_customer_user_context(
    db: SupabaseAdmin,
    customer_id: str,
    account_id: str,
    context_text: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    existing = await get_customer_user_context(db, customer_id)
    metadata = {"context": context_text}
    if existing:
        updated_rows = await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_context",
            params={
                "customer_context_id": f"eq.{existing['customer_context_id']}",
                "select": "customer_context_id,customer_id,site_id,account_id,event_type,metadata,created_at,updated_at",
            },
            json_body={
                "account_id": account_id,
                "metadata": metadata,
                "updated_at": now,
            },
            headers={"Prefer": "return=representation"},
        )
        return updated_rows[0]
    created_rows = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_context",
        params={"select": "customer_context_id,customer_id,site_id,account_id,event_type,metadata,created_at,updated_at"},
        json_body={
            "customer_id": customer_id,
            "site_id": None,
            "account_id": account_id,
            "event_type": "user_context",
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        },
        headers={"Prefer": "return=representation"},
    )
    return created_rows[0]


async def upsert_account(db: SupabaseAdmin, org_name: str, domain: str) -> dict[str, str]:
    account_rows = await db.request(
        "GET",
        "/rest/v1/accounts",
        params={
            "select": "account_id,company_name,account_domain",
            "account_domain": f"eq.{domain}",
            "limit": 1,
        },
    )
    if account_rows:
        account = account_rows[0]
        if not account.get("company_name") and org_name:
            await db.request(
                "PATCH",
                "/rest/v1/accounts",
                params={"account_id": f"eq.{account['account_id']}"},
                json_body={"company_name": org_name},
                headers={"Prefer": "return=minimal"},
            )
        return {
            "accountId": account["account_id"],
            "companyName": account.get("company_name") or org_name,
            "domain": account.get("account_domain") or domain,
        }
    created = await db.request(
        "POST",
        "/rest/v1/accounts",
        params={"select": "account_id,company_name,account_domain"},
        json_body={"company_name": org_name, "account_domain": domain},
        headers={"Prefer": "return=representation"},
    )
    row = created[0]
    return {
        "accountId": row["account_id"],
        "companyName": row.get("company_name") or org_name,
        "domain": row.get("account_domain") or domain,
    }


async def find_duplicate_account_site(db: SupabaseAdmin, account_id: str, site: dict[str, str]) -> dict[str, Any] | None:
    data = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,full_address,street,city,state,zip,metadata",
            "account_id": f"eq.{account_id}",
            "is_archived": "eq.false",
            "limit": 5000,
        },
    )
    candidate_address = normalized_structured_site(
        {
            "fullAddress": site["fullAddress"],
            "street": site["street"],
            "city": site["city"],
            "state": site["state"],
            "zip": site["zip"],
        }
    )
    for row in data or []:
        existing_address = normalized_structured_site(
            {
                "fullAddress": row.get("full_address"),
                "street": row.get("street"),
                "city": row.get("city"),
                "state": row.get("state"),
                "zip": row.get("zip"),
            }
        )
        if (
            candidate_address["fullAddress"]
            and candidate_address["fullAddress"] == existing_address["fullAddress"]
        ) or same_structured_site(candidate_address, existing_address):
            return row
    return None


async def find_customer_site_assignment(
    db: SupabaseAdmin,
    customer_id: str,
    customer_site_id: str,
) -> dict[str, Any] | None:
    """Find a customer site assignment by customer_id and customer_site_id."""
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,customer_id,site_id,account_id,assigned_via,metadata,recommendations,notes,report_metadata,report_context_high,report_context_all,rating_metadata,is_report_ready,created_at",
            "customer_id": f"eq.{customer_id}",
            "customer_site_id": f"eq.{customer_site_id}",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def ensure_customer_site_assignment(
    db: SupabaseAdmin,
    customer_id: str,
    account_id: str,
    site_id: str,
    *,
    assigned_via: str = "user_added_site",
    requested_at: str | None = None,
    generated_at: str | None = None,
    address_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Only check for existing owned sites, not shared sites
    # This allows users to add their own copy of a shared site
    query_params = {
        "select": "customer_site_id,metadata",
        "customer_id": f"eq.{customer_id}",
        "site_id": f"eq.{site_id}",
        "limit": 1,
    }
    
    # For owned sites, only check for other owned sites
    # For shared sites, check for the exact share (handled by unique constraint)
    if assigned_via in ("user_added_site", "dev_added_site"):
        query_params["assigned_via"] = f"in.(user_added_site,dev_added_site)"
    else:
        query_params["assigned_via"] = f"eq.{assigned_via}"
    
    existing = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params=query_params,
    )
    row = existing[0] if existing else None
    metadata = build_customer_site_metadata(
        row.get("metadata") if row else None,
        requested_at=requested_at,
        generated_at=generated_at,
        address_validation=address_validation,
    )
    payload = {
        "customer_id": customer_id,
        "site_id": site_id,
        "account_id": account_id,
        "assigned_via": assigned_via,
        "metadata": metadata,
    }
    if requested_at:
        payload["is_report_ready"] = False
    if generated_at:
        payload["is_report_ready"] = True
    if row:
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_sites",
            params={"customer_site_id": f"eq.{row['customer_site_id']}"},
            json_body=payload,
            headers={"Prefer": "return=minimal"},
        )
        return {"customerSiteId": row["customer_site_id"], "metadata": metadata, "wasExisting": True}
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_sites",
        params={"select": "customer_site_id,metadata"},
        json_body=payload,
        headers={"Prefer": "return=representation"},
    )
    result = created[0]
    return {"customerSiteId": result["customer_site_id"], "metadata": result.get("metadata") or metadata, "wasExisting": False}


_SHARE_SOURCE_SELECT = (
    "customer_site_id,site_id,account_id,metadata,recommendations,"
    "report_metadata,report_context_high,report_context_all,is_report_ready,assigned_via"
)


def _is_share_source_row(row: dict[str, Any] | None, site_id: str) -> bool:
    return bool(
        row
        and row.get("site_id") == site_id
        and row.get("is_report_ready")
    )


def _shared_snapshot_needs_backfill(existing_row: dict[str, Any]) -> bool:
    if not existing_row.get("is_report_ready"):
        return True
    if not (existing_row.get("report_metadata") or {}):
        return True
    if not (existing_row.get("report_context_high") or {}):
        return True
    if not (existing_row.get("report_context_all") or {}):
        return True
    return False


async def find_share_source_assignment(
    db: SupabaseAdmin,
    site_id: str,
    shared_by_customer_id: str | None = None,
    *,
    source_customer_site_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the sharer's report row to copy — never another customer's assignment."""
    if source_customer_site_id and shared_by_customer_id:
        explicit = await find_customer_site_assignment(
            db, shared_by_customer_id, source_customer_site_id
        )
        if _is_share_source_row(explicit, site_id):
            return explicit

    if shared_by_customer_id:
        owned_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "select": _SHARE_SOURCE_SELECT,
                "customer_id": f"eq.{shared_by_customer_id}",
                "site_id": f"eq.{site_id}",
                "is_report_ready": "eq.true",
                "assigned_via": "in.(user_added_site,dev_added_site)",
                "limit": 1,
            },
        )
        if owned_rows:
            return owned_rows[0]

        shared_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "select": _SHARE_SOURCE_SELECT,
                "customer_id": f"eq.{shared_by_customer_id}",
                "site_id": f"eq.{site_id}",
                "is_report_ready": "eq.true",
                "assigned_via": "eq.shared_site",
                "limit": 1,
            },
        )
        if shared_rows:
            return shared_rows[0]
        return None

    # Legacy share tokens without shared_by — fall back to any ready owned row for the site.
    legacy_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": _SHARE_SOURCE_SELECT,
            "site_id": f"eq.{site_id}",
            "is_report_ready": "eq.true",
            "assigned_via": "eq.user_added_site",
            "order": "created_at.asc",
            "limit": 1,
        },
    )
    return legacy_rows[0] if legacy_rows else None


async def ensure_shared_site_assignment(
    db: SupabaseAdmin,
    customer_id: str,
    site_id: str,
    shared_by_customer_id: str | None = None,
    *,
    source_customer_site_id: str | None = None,
) -> dict[str, Any]:
    """Ensure shared site assignment exists for recipient. Allows multiple shares from different users."""

    async def load_share_source() -> dict[str, Any] | None:
        return await find_share_source_assignment(
            db,
            site_id,
            shared_by_customer_id,
            source_customer_site_id=source_customer_site_id,
        )

    def shared_payload_from_source(source: dict[str, Any]) -> dict[str, Any]:
        shared_metadata = dict(source.get("metadata") or {})
        shared_metadata.pop("last_pre_assessment_requested_at", None)
        return {
            "metadata": shared_metadata,
            "recommendations": source.get("recommendations") or {},
            "report_metadata": source.get("report_metadata") or {},
            "report_context_high": source.get("report_context_high") or {},
            "report_context_all": source.get("report_context_all") or {},
            "is_report_ready": bool(source.get("is_report_ready")),
        }

    # Check if this specific (recipient, site, sharer) combo already exists
    if shared_by_customer_id:
        existing = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "select": "customer_site_id,site_id,account_id,assigned_via,shared_by,is_report_ready,report_metadata,report_context_high,report_context_all",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "assigned_via": "eq.shared_site",
                "shared_by": f"eq.{shared_by_customer_id}",
                "limit": 1,
            },
        )
        if existing:
            existing_row = existing[0]
            source = await load_share_source()
            if source and _shared_snapshot_needs_backfill(existing_row):
                await db.request(
                    "PATCH",
                    "/rest/v1/automatisor_customer_sites",
                    params={"customer_site_id": f"eq.{existing_row['customer_site_id']}"},
                    json_body=shared_payload_from_source(source),
                    headers={"Prefer": "return=minimal"},
                )
            return existing_row

    source = await load_share_source()
    if not source:
        raise HTTPException(status_code=404, detail="The shared report is no longer available.")

    json_body = {
        "customer_id": customer_id,
        "site_id": source["site_id"],
        "account_id": source["account_id"],
        "assigned_via": "shared_site",
        **shared_payload_from_source(source),
    }
    if shared_by_customer_id:
        json_body["shared_by"] = shared_by_customer_id

    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_sites",
        params={"select": "customer_site_id,site_id,account_id,assigned_via,shared_by"},
        json_body=json_body,
        headers={"Prefer": "return=representation"},
    )
    return created[0]


async def find_owned_customer_site_assignment(
    db: SupabaseAdmin,
    customer_id: str,
    site_id: str,
) -> dict[str, Any] | None:
    """Return the customer's owned assignment for a site, if one exists."""
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,metadata",
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "assigned_via": "in.(user_added_site,dev_added_site)",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def insert_account_site_if_missing(
    db: SupabaseAdmin,
    account_id: str,
    company_name: str,
    site: dict[str, str],
    customer_id: str,
    *,
    customer_site_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    duplicate_row = await find_duplicate_account_site(db, account_id, site)
    if duplicate_row:
        site_id = duplicate_row["site_id"]
    else:
        created = await db.request(
            "POST",
            "/rest/v1/account_sites",
            params={"select": "site_id"},
            json_body=site_insert_row(account_id, company_name, site),
            headers={"Prefer": "return=representation"},
        )
        site_id = created[0].get("site_id") if created else None

    if not site_id:
        raise HTTPException(status_code=500, detail="Could not save site")

    # Check before writing — do not create/update the assignment first
    existing_owned = await find_owned_customer_site_assignment(db, customer_id, site_id)
    if existing_owned:
        metadata = existing_owned.get("metadata") or {}
        already_requested = bool(metadata.get("last_pre_assessment_requested_at"))
        return {
            "status": "already_exists",
            "siteId": site_id,
            "customerSiteId": existing_owned["customer_site_id"],
            "can_proceed": not already_requested,
            "reason": "owned_assignment_exists",
        }

    assignment = await ensure_customer_site_assignment(
        db,
        customer_id,
        account_id,
        site_id,
        address_validation=customer_site_validation,
    )
    return {
        "status": "created",
        "siteId": site_id,
        "customerSiteId": assignment["customerSiteId"],
        "can_proceed": True,
        "reason": "created",
    }


async def build_workspace_state(db: SupabaseAdmin, email: str, requested_account_id: str | None = None) -> dict[str, Any]:
    customer = await find_customer_by_email(db, email)
    if not customer:
        return {
            "email": email,
            "user_mode": "new_user",
            "next_step": "onboarding",
            "customer_id": None,
            "account_id": None,
            "active_account_id": None,
            "accounts": [],
            "company_name": "",
            "company_domain": "",
            "credits_used_total": 0,
            "credits_used_this_month": 0,
        }
    linked_accounts = await list_customer_accounts(db, customer)
    site_counts = await count_customer_sites_by_account(db, [row["account_id"] for row in linked_accounts], customer["customer_id"])
    active_account = choose_active_account(linked_accounts, requested_account_id)
    accounts = [
        {
            **row,
            "site_count": site_counts.get(row["account_id"], 0),
            "is_active": bool(active_account and row["account_id"] == active_account["account_id"]),
        }
        for row in linked_accounts
    ]
    usage = await get_customer_usage_state(db, customer["customer_id"])
    return {
        "email": email,
        "user_mode": "existing_user",
        "next_step": "workspace",
        "customer_id": customer["customer_id"],
        "account_id": active_account["account_id"] if active_account else None,
        "active_account_id": active_account["account_id"] if active_account else None,
        "accounts": accounts,
        "company_name": (active_account.get("company_name") if active_account else "") or customer.get("company_name") or "",
        "company_domain": (active_account.get("company_domain") if active_account else "") or customer.get("company_domain") or "",
        "first_name": customer.get("first_name") or "",
        "last_name": customer.get("last_name") or "",
        "designation": customer.get("designation") or "",
        "credits_used_total": usage["creditsUsedTotal"],
        "credits_used_this_month": usage["creditsUsedThisMonth"],
    }


async def build_workspace_payload(db: SupabaseAdmin, email: str, requested_account_id: str | None = None) -> dict[str, Any]:
    workspace = await build_workspace_state(db, email, requested_account_id)
    sites = await list_customer_sites(db, workspace.get("customer_id"))
    wishlist = await list_customer_wishlist(db, workspace.get("customer_id"))
    return {**workspace, "sites": sites, "wishlist": wishlist, "pre_assessment_price_credits": PRE_ASSESSMENT_PRICE}


async def get_customer_usage_state(db: SupabaseAdmin, customer_id: str | None) -> dict[str, int]:
    if not customer_id:
        return {"creditsUsedTotal": 0, "creditsUsedThisMonth": 0}
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_billing",
        params={
            "select": "credits_used,created_at",
            "customer_id": f"eq.{customer_id}",
        },
    )
    now = datetime.now(timezone.utc)
    total = 0
    current = 0
    for row in rows or []:
        used = int(row.get("credits_used") or 0)
        total += used
        created_at_raw = row.get("created_at") or ""
        try:
            created_at = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
        except ValueError:
            created_at = None
        if created_at and created_at.year == now.year and created_at.month == now.month:
            current += used
    return {"creditsUsedTotal": total, "creditsUsedThisMonth": current}


async def insert_billing_usage(
    db: SupabaseAdmin,
    *,
    customer_id: str,
    account_id: str | None,
    site_id: str | None,
    usage_type: str,
    credits_used: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    # First report for this customer is always free.
    prior_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_billing",
        params={"select": "billing_id", "customer_id": f"eq.{customer_id}", "limit": 1},
    )
    is_first = not bool(prior_rows)
    await db.request(
        "POST",
        "/rest/v1/automatisor_billing",
        json_body={
            "customer_id": customer_id,
            "account_id": account_id,
            "site_id": site_id,
            "usage_type": usage_type,
            "credits_used": credits_used,
            "metadata": metadata or {},
            "is_free": is_first,
        },
        headers={"Prefer": "return=minimal"},
    )


def escape_html(value: Any) -> str:
    raw = str(value or "")
    return (
        raw.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def escape_slack_mrkdwn(value: Any) -> str:
    raw = str(value or "")
    return raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_pre_assessment_approval_email(email: str, company_name: str, site_address: str) -> str:
    safe_email = escape_html(email)
    safe_company = escape_html(company_name or "your company")
    safe_address = escape_html(site_address or "your site")
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f4ef;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4ef;padding:40px 16px">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:560px;width:100%">
        <tr>
          <td style="background:#030149;padding:24px 32px">
            <span style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:-0.3px">AutomatiSOR</span>
            <span style="color:#f25c19;font-size:18px;font-weight:700"> ·</span>
          </td>
        </tr>
        <tr>
          <td style="padding:36px 32px 28px">
            <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#030149;letter-spacing:-0.4px">
              We received your request ✓
            </h1>
            <p style="margin:0 0 14px;font-size:15px;color:#4a4a44;line-height:1.65">
              Thanks — we’ve received your AutomatiSOR site pre-assessment report request.
              The job is now running, and we’ll notify you by email once the report has been generated.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px">
              <tr>
                <td style="padding:12px 14px;border:1px solid #ebebea;border-radius:10px;background:#fbfbfa">
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Company</div>
                  <div style="font-size:15px;color:#030149;font-weight:700;letter-spacing:-0.2px">{safe_company}</div>
                  <div style="height:10px"></div>
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Site address</div>
                  <div style="font-size:14px;color:#030149;font-weight:600">{safe_address}</div>
                </td>
              </tr>
            </table>
            <p style="margin:0;font-size:13px;color:#7a7a72;line-height:1.6">
              If anything looks off, just reply to this email and our team will help.
            </p>
          </td>
        </tr>
        <tr><td style="padding:0 32px"><hr style="border:none;border-top:1px solid #ebebea;margin:0"></td></tr>
        <tr>
          <td style="padding:20px 32px;text-align:center">
            <p style="margin:0;font-size:12px;color:#a0a09a;line-height:1.6">
              AutomatiSOR<br>
              Questions? Reply to this email or contact
              <a href="mailto:notifications@automatisor.com" style="color:#7a7a72">notifications@automatisor.com</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_pre_assessment_approval_email(email: str, company_name: str, site_address: str) -> None:
    from_email = _require_resend_config()
    try:
        await _deliver_resend_email(
            {
                "from": from_email,
                "to": [email],
                "subject": "Your AutomatiSOR site pre-assessment report request is confirmed!",
                "html": build_pre_assessment_approval_email(email, company_name, site_address),
            },
            failure_label="Failed to send approval email",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def build_report_share_email(sharer_email: str, company_name: str, site_address: str, share_url: str) -> str:
    """Build HTML email for report sharing - matches pre-assessment email design."""
    safe_sharer = html.escape(sharer_email)
    safe_company = html.escape(company_name or "Site pre-assessment")
    safe_address = html.escape(site_address or "")
    safe_url = html.escape(share_url, quote=True)
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f6f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 16px;background:#f6f7fb">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:560px;width:100%">
        <tr>
          <td style="background:#030149;padding:24px 32px">
            <span style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:-0.3px">AutomatiSOR</span>
            <span style="color:#f25c19;font-size:18px;font-weight:700"> ·</span>
          </td>
        </tr>
        <tr>
          <td style="padding:36px 32px 28px">
            <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#030149;letter-spacing:-0.4px">
              Report shared with you
            </h1>
            <p style="margin:0 0 14px;font-size:15px;color:#4a4a44;line-height:1.65">
              {safe_sharer} shared an AutomatiSOR facility pre-assessment report with you.
              Sign in to view the report on the app.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px">
              <tr>
                <td style="padding:12px 14px;border:1px solid #ebebea;border-radius:10px;background:#fbfbfa">
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Company</div>
                  <div style="font-size:15px;color:#030149;font-weight:700;letter-spacing:-0.2px">{safe_company}</div>
                  <div style="height:10px"></div>
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Site address</div>
                  <div style="font-size:14px;color:#030149;font-weight:600">{safe_address}</div>
                </td>
              </tr>
            </table>
            <table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0">
              <tr>
                <td align="center">
                  <a href="{safe_url}" style="display:inline-block;padding:14px 28px;border-radius:10px;background:#f25c19;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;letter-spacing:-0.2px">View report</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr><td style="padding:0 32px"><hr style="border:none;border-top:1px solid #ebebea;margin:0"></td></tr>
        <tr>
          <td style="padding:20px 32px;text-align:center">
            <p style="margin:0;font-size:12px;color:#a0a09a;line-height:1.6">
              AutomatiSOR<br>
              Questions? Reply to this email or contact
              <a href="mailto:notifications@automatisor.com" style="color:#7a7a72">notifications@automatisor.com</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_report_share_email(
    recipient_email: str,
    sharer_email: str,
    company_name: str,
    site_address: str,
    share_url: str,
) -> None:
    """Send share report email via Resend."""
    from_email = _require_resend_config()
    await _deliver_resend_email(
        {
            "from": from_email,
            "to": [recipient_email],
            "subject": f"{sharer_email} shared an AutomatiSOR report with you",
            "html": build_report_share_email(sharer_email, company_name, site_address, share_url),
        },
        failure_label="Failed to send share email",
    )


async def find_chat_share_source_assignment(
    db: SupabaseAdmin,
    site_id: str,
    shared_by_customer_id: str,
) -> dict[str, Any] | None:
    """Return the sharer's site assignment for chat sharing (report need not be ready)."""
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": _SHARE_SOURCE_SELECT,
            "customer_id": f"eq.{shared_by_customer_id}",
            "site_id": f"eq.{site_id}",
            "order": "created_at.desc",
            "limit": 1,
        },
    )
    return rows[0] if rows else None


async def ensure_chat_shared_site_assignment(
    db: SupabaseAdmin,
    customer_id: str,
    site_id: str,
    shared_by_customer_id: str,
) -> dict[str, Any]:
    """Ensure recipient has site access when a chat conversation is shared."""

    async def load_share_source() -> dict[str, Any] | None:
        return await find_chat_share_source_assignment(db, site_id, shared_by_customer_id)

    def shared_payload_from_source(source: dict[str, Any]) -> dict[str, Any]:
        shared_metadata = dict(source.get("metadata") or {})
        shared_metadata.pop("last_pre_assessment_requested_at", None)
        return {
            "metadata": shared_metadata,
            "recommendations": source.get("recommendations") or {},
            "report_metadata": source.get("report_metadata") or {},
            "report_context_high": source.get("report_context_high") or {},
            "report_context_all": source.get("report_context_all") or {},
            "is_report_ready": bool(source.get("is_report_ready")),
        }

    existing = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,site_id,account_id,assigned_via,shared_by,is_report_ready,report_metadata,report_context_high,report_context_all",
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "assigned_via": "eq.shared_site",
            "shared_by": f"eq.{shared_by_customer_id}",
            "limit": 1,
        },
    )
    if existing:
        existing_row = existing[0]
        source = await load_share_source()
        if source and _shared_snapshot_needs_backfill(existing_row):
            await db.request(
                "PATCH",
                "/rest/v1/automatisor_customer_sites",
                params={"customer_site_id": f"eq.{existing_row['customer_site_id']}"},
                json_body=shared_payload_from_source(source),
                headers={"Prefer": "return=minimal"},
            )
        return existing_row

    source = await load_share_source()
    if not source:
        raise HTTPException(status_code=404, detail="The shared site is no longer available.")

    json_body = {
        "customer_id": customer_id,
        "site_id": source["site_id"],
        "account_id": source["account_id"],
        "assigned_via": "shared_site",
        "shared_by": shared_by_customer_id,
        **shared_payload_from_source(source),
    }
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_sites",
        params={"select": "customer_site_id,site_id,account_id,assigned_via,shared_by"},
        json_body=json_body,
        headers={"Prefer": "return=representation"},
    )
    return created[0]


def snapshot_chat_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    return json.loads(json.dumps(messages))


async def create_chat_mirror_for_share(
    db: SupabaseAdmin,
    *,
    site_id: str,
    title: str | None,
    messages: list[dict[str, Any]],
    source_session_id: str,
    shared_by_customer_id: str,
    customer_id: str | None = None,
    pending_recipient_email: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    json_body: dict[str, Any] = {
        "site_id": site_id,
        "chat_type": "site",
        "title": title,
        "messages": snapshot_chat_messages(messages),
        "source_session_id": source_session_id,
        "shared_by_customer_id": shared_by_customer_id,
        "shared_at": now,
        "created_at": now,
        "updated_at": now,
        "is_archived": False,
    }
    if customer_id:
        json_body["customer_id"] = customer_id
    if pending_recipient_email:
        json_body["pending_recipient_email"] = pending_recipient_email
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_chatbot",
        params={
            "select": "session_id,customer_id,site_id,title,pending_recipient_email,source_session_id,shared_by_customer_id,shared_at,created_at,updated_at",
        },
        json_body=json_body,
        headers={"Prefer": "return=representation"},
    )
    return created[0]


async def fulfill_pending_chat_shares(db: SupabaseAdmin, email: str, customer_id: str) -> None:
    normalized = normalize_email(email)
    if not normalized or not customer_id:
        return
    pending_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_chatbot",
        params={
            "select": "session_id",
            "pending_recipient_email": f"eq.{normalized}",
            "customer_id": "is.null",
        },
    )
    if not pending_rows:
        return
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_chatbot",
        params={
            "pending_recipient_email": f"eq.{normalized}",
            "customer_id": "is.null",
        },
        json_body={
            "customer_id": customer_id,
            "pending_recipient_email": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Prefer": "return=minimal"},
    )


def build_chat_share_email(
    sharer_email: str,
    company_name: str,
    site_address: str,
    chat_title: str,
    share_url: str,
) -> str:
    """Build HTML email for chat conversation sharing."""
    safe_sharer = html.escape(sharer_email)
    safe_company = html.escape(company_name or "Site")
    safe_address = html.escape(site_address or "")
    safe_title = html.escape(chat_title or "Shared conversation")
    safe_url = html.escape(share_url, quote=True)
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f6f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 16px;background:#f6f7fb">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;max-width:560px;width:100%">
        <tr>
          <td style="background:#030149;padding:24px 32px">
            <span style="color:#ffffff;font-size:18px;font-weight:700;letter-spacing:-0.3px">AutomatiSOR</span>
            <span style="color:#f25c19;font-size:18px;font-weight:700"> ·</span>
          </td>
        </tr>
        <tr>
          <td style="padding:36px 32px 28px">
            <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#030149;letter-spacing:-0.4px">
              Conversation shared with you
            </h1>
            <p style="margin:0 0 14px;font-size:15px;color:#4a4a44;line-height:1.65">
              {safe_sharer} shared an AutomatiSOR report conversation with you.
              Sign in to view the site report and the shared chat history.
            </p>
            <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px">
              <tr>
                <td style="padding:12px 14px;border:1px solid #ebebea;border-radius:10px;background:#fbfbfa">
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Conversation</div>
                  <div style="font-size:15px;color:#030149;font-weight:700;letter-spacing:-0.2px">{safe_title}</div>
                  <div style="height:10px"></div>
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Company</div>
                  <div style="font-size:15px;color:#030149;font-weight:700;letter-spacing:-0.2px">{safe_company}</div>
                  <div style="height:10px"></div>
                  <div style="font-size:12px;color:#7a7a72;margin-bottom:6px">Site address</div>
                  <div style="font-size:14px;color:#030149;font-weight:600">{safe_address}</div>
                </td>
              </tr>
            </table>
            <table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0">
              <tr>
                <td align="center">
                  <a href="{safe_url}" style="display:inline-block;padding:14px 28px;border-radius:10px;background:#f25c19;color:#ffffff;text-decoration:none;font-size:15px;font-weight:700;letter-spacing:-0.2px">View conversation</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr><td style="padding:0 32px"><hr style="border:none;border-top:1px solid #ebebea;margin:0"></td></tr>
        <tr>
          <td style="padding:20px 32px;text-align:center">
            <p style="margin:0;font-size:12px;color:#a0a09a;line-height:1.6">
              AutomatiSOR<br>
              Questions? Reply to this email or contact
              <a href="mailto:notifications@automatisor.com" style="color:#7a7a72">notifications@automatisor.com</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_chat_share_email(
    recipient_email: str,
    sharer_email: str,
    company_name: str,
    site_address: str,
    chat_title: str,
    share_url: str,
) -> None:
    """Send shared chat email via Resend (same delivery path as report share)."""
    from_email = _require_resend_config()
    await _deliver_resend_email(
        {
            "from": from_email,
            "to": [recipient_email],
            "subject": f"{sharer_email} shared an AutomatiSOR conversation with you",
            "html": build_chat_share_email(
                sharer_email,
                company_name,
                site_address,
                chat_title,
                share_url,
            ),
        },
        failure_label="Failed to send chat share email",
    )


async def resolve_sender_report_assignment(
    db: SupabaseAdmin,
    customer_id: str,
    *,
    customer_site_id: str | None = None,
    site_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the sender's report row for sharing, with site_id fallback."""
    if customer_site_id:
        assignment = await find_customer_site_assignment(db, customer_id, customer_site_id)
        if assignment and (not site_id or assignment.get("site_id") == site_id):
            return assignment
    if site_id:
        return await find_share_source_assignment(
            db,
            site_id,
            customer_id,
            source_customer_site_id=customer_site_id,
        )
    return None


def validate_share_recipients(raw_emails: Any, sender_email: str) -> tuple[list[str], list[dict[str, str]]]:
    """Validate share recipient emails."""
    values = raw_emails if isinstance(raw_emails, list) else re.split(r"[,;\s]+", str(raw_emails or ""))
    valid: list[str] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in values:
        email = normalize_email(raw)
        if not email:
            continue
        if email in seen:
            errors.append({"email": email, "reason": "Duplicate address"})
            continue
        seen.add(email)
        if email == sender_email:
            errors.append({"email": email, "reason": "You cannot share a report with yourself"})
            continue
        try:
            valid.append(assert_work_email(email))
        except ValueError as exc:
            errors.append({"email": email, "reason": str(exc)})
    if not valid and not errors:
        errors.append({"email": "", "reason": "Enter at least one work email"})
    return valid, errors


async def send_slack_pre_assessment_notification(
    *,
    company_name: str,
    site_address: str,
    contact_name: str,
    email: str,
    designation: str,
    account_id: str | None,
    site_id: str | None,
    customer_site_id: str | None,
) -> None:
    if not SLACK_WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL not set; skipping Slack notification")
        return

    company = company_name or "N/A"
    site = site_address or "N/A"
    contact = contact_name or "N/A"
    contact_email = email or "N/A"
    title = designation or "N/A"
    submitted_at = datetime.now(timezone.utc).isoformat()

    text = "\n".join(
        [
            "New AutomatiSOR pre-assessment request",
            f"- Company: {company}",
            f"- Site: {site}",
            f"- Contact: {contact}",
            f"- Email: {contact_email}",
            f"- Title: {title}",
        ]
    )
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "New AutomatiSOR pre-assessment request",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Company*\n{escape_slack_mrkdwn(company)}"},
                {"type": "mrkdwn", "text": f"*Contact*\n{escape_slack_mrkdwn(contact)}"},
                {"type": "mrkdwn", "text": f"*Email*\n{escape_slack_mrkdwn(contact_email)}"},
                {"type": "mrkdwn", "text": f"*Title*\n{escape_slack_mrkdwn(title)}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Site address*\n{escape_slack_mrkdwn(site)}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*account_id*\n{escape_slack_mrkdwn(account_id or 'N/A')}"},
                {"type": "mrkdwn", "text": f"*site_id*\n{escape_slack_mrkdwn(site_id or 'N/A')}"},
                {
                    "type": "mrkdwn",
                    "text": f"*customer_site_id*\n{escape_slack_mrkdwn(customer_site_id or 'N/A')}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Submitted at: {escape_slack_mrkdwn(submitted_at)}",
                }
            ],
        },
        {"type": "divider"},
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            SLACK_WEBHOOK_URL,
            json={"text": text, "blocks": blocks},
            headers={"Content-Type": "application/json"},
        )
    if response.is_error:
        raise RuntimeError(f"Slack webhook error {response.status_code}: {response.text}")


async def send_supabase_otp(email: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_AUTH_SEND_TIMEOUT) as client:
            response = await client.post(
                f"{get_supabase_auth_url()}/otp",
                headers=get_auth_headers(),
                json={
                    "email": email,
                    "create_user": True,
                },
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="We could not confirm the OTP was sent. Please try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not reach the authentication service. Please try again.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if response.is_error:
        detail = _extract_supabase_error(response)
        raise HTTPException(status_code=infer_error_status(detail), detail=detail)


def _supabase_auth_response(session: Any, user: Any) -> dict[str, Any]:
    return {
        "session": {
            "access_token": getattr(session, "access_token", None),
            "refresh_token": getattr(session, "refresh_token", None),
            "expires_in": getattr(session, "expires_in", None),
            "token_type": getattr(session, "token_type", None),
        }
        if session
        else None,
        "user": {"id": getattr(user, "id", None)} if user else None,
    }


async def verify_supabase_otp(email: str, otp: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_AUTH_VERIFY_TIMEOUT) as client:
            response = await client.post(
                f"{get_supabase_auth_url()}/verify",
                headers=get_auth_headers(),
                json={
                    "email": email,
                    "token": otp,
                    "type": "email",
                },
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="We could not verify the OTP because the authentication service timed out. Please try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not reach the authentication service. Please try again.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if response.is_error:
        detail = _extract_supabase_error(response) or "Invalid or expired OTP"
        raise HTTPException(status_code=infer_error_status(detail), detail=detail)
    return _supabase_auth_json_response(response.json())


async def mint_supabase_session_for_email(email: str) -> dict[str, Any]:
    """Issue a Supabase session without sending email (trusted allowlist only)."""
    db = get_admin_db()
    link_payload = await db.request(
        "POST",
        "/auth/v1/admin/generate_link",
        json_body={"type": "magiclink", "email": email},
    )
    payload = link_payload or {}
    nested = payload.get("properties") or {}
    email_otp = payload.get("email_otp") or nested.get("email_otp")
    if not email_otp:
        raise HTTPException(status_code=500, detail="Could not create trusted login session")
    try:
        auth = get_auth_client()
        response = auth.auth.verify_otp(
            {
                "email": email,
                "token": str(email_otp),
                "type": "email",
            }
        )
        return _supabase_auth_response(response.session, response.user)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc) or "Could not create trusted login session") from exc


@app.get("/api/frontend-config")
async def frontend_config() -> dict[str, Any]:
    return {
        "google_maps_api_key": GOOGLE_MAPS_API_KEY,
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
    }


@app.post("/api/address-validation/check")
async def check_address_validation(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        company_name = clean_required(body.get("company_name") or body.get("company"), "Company name")
        domain = clean_required(body.get("domain") or body.get("company_domain"), "Company domain")
        address = clean_required(body.get("address") or body.get("full_address"), "Site address")
        result = validate_company_site(company_name, address, domain, api_key=GOOGLE_MAPS_API_KEY)
        return {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/auth/check-email")
async def handle_check_email(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        return {
            "email": email,
            "user_mode": "existing_user" if customer else "new_user",
            "company_email": True,
            "trusted_bypass": is_trusted_bypass_email(email),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/auth/request-otp")
async def handle_request_otp(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if is_trusted_bypass_email(email):
            return {
                "status": "trusted_bypass",
                "email": email,
                "user_mode": "existing_user" if customer else "new_user",
                "trusted_bypass": True,
                "dry_run": is_dry_run_request(request, body),
            }
        await send_supabase_otp(email)
        return {
            "status": "otp_sent",
            "email": email,
            "user_mode": "existing_user" if customer else "new_user",
            "dry_run": is_dry_run_request(request, body),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _session_from_supabase_auth_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    session = payload.get("session")
    if isinstance(session, dict):
        return session
    if payload.get("access_token"):
        return {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "expires_in": payload.get("expires_in"),
            "token_type": payload.get("token_type"),
        }
    return None


def _user_from_supabase_auth_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    user = payload.get("user")
    if isinstance(user, dict):
        return {"id": user.get("id")}
    return None


def _supabase_auth_json_response(payload: dict[str, Any]) -> dict[str, Any]:
    session = _session_from_supabase_auth_payload(payload)
    return {
        "session": {
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "expires_in": session.get("expires_in"),
            "token_type": session.get("token_type"),
        }
        if session
        else None,
        "user": _user_from_supabase_auth_payload(payload),
    }


ACCESS_TOKEN_COOKIE_MAX_AGE = 60 * 60
REFRESH_TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def set_auth_cookies(response: Response, session: dict[str, Any]) -> None:
    access_token = clean_optional(session.get("access_token"))
    if not access_token:
        return
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=ACCESS_TOKEN_COOKIE_MAX_AGE,
    )
    refresh_token = clean_optional(session.get("refresh_token"))
    if refresh_token:
        response.set_cookie(
            "refresh_token",
            refresh_token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            path="/",
            max_age=REFRESH_TOKEN_COOKIE_MAX_AGE,
        )


def clear_auth_cookies(response: Response) -> None:
    cookie_kwargs = {
        "httponly": True,
        "secure": COOKIE_SECURE,
        "samesite": "lax",
        "path": "/",
    }
    response.delete_cookie("access_token", **cookie_kwargs)
    response.delete_cookie("refresh_token", **cookie_kwargs)


async def refresh_supabase_session(refresh_token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=SUPABASE_AUTH_VERIFY_TIMEOUT) as client:
            response = await client.post(
                f"{get_supabase_auth_url()}/token?grant_type=refresh_token",
                headers=get_auth_headers(),
                json={"refresh_token": refresh_token},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="We could not refresh your session because the authentication service timed out. Please try again.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not reach the authentication service. Please try again.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if response.is_error:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    return _supabase_auth_json_response(response.json())


async def complete_auth_for_verified_email(
    response: Response,
    request: Request,
    email: str,
    auth_data: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    session = auth_data.get("session") or {}
    if not session.get("access_token"):
        raise HTTPException(status_code=422, detail="Invalid or expired OTP")
    set_auth_cookies(response, session)
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    share_payload = decode_share_token(body.get("share_token"))
    matching_share = share_payload if share_payload and share_payload["recipient_email"] == email else None

    if matching_share and not is_dry_run_request(request, body):
        if not customer:
            return {
                "status": "verified",
                "email": email,
                "user_mode": "new_user",
                "next_step": "onboarding_step1",
                "share_token": body.get("share_token"),
                "customer_id": None,
                "account_id": None,
                "credits_used_total": 0,
                "credits_used_this_month": 0,
            }
        await mark_customer_verified(db, email)
        await touch_customer_login(db, customer["customer_id"])
        if matching_share.get("share_type") == "chat":
            shared_assignment = await ensure_chat_shared_site_assignment(
                db,
                customer["customer_id"],
                matching_share["site_id"],
                matching_share.get("shared_by") or "",
            )
        else:
            shared_assignment = await ensure_shared_site_assignment(
                db, customer["customer_id"], matching_share["site_id"], matching_share.get("shared_by")
            )
        await fulfill_pending_chat_shares(db, email, customer["customer_id"])
        workspace = await build_workspace_payload(db, email)
        return {
            "status": "verified",
            "user_id": (auth_data.get("user") or {}).get("id"),
            **workspace,
            "share_destination": {
                "customer_site_id": shared_assignment["customer_site_id"],
                "site_id": shared_assignment["site_id"],
                "account_id": shared_assignment["account_id"],
            },
        }

    if not customer:
        return {
            "status": "verified",
            "email": email,
            "user_mode": "new_user",
            "next_step": "onboarding_step1",
            "customer_id": None,
            "account_id": None,
            "credits_used_total": 0,
            "credits_used_this_month": 0,
        }
    if not is_dry_run_request(request, body):
        await mark_customer_verified(db, email)
        await touch_customer_login(db, customer["customer_id"])
    workspace = await build_workspace_state(db, email)
    return {"status": "verified", "user_id": (auth_data.get("user") or {}).get("id"), **workspace}


@app.post("/api/auth/verify-otp")
async def handle_verify_otp(response: Response, request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        otp = str(body.get("otp") or "").strip()
        if not re.fullmatch(r"\d{6}", otp):
            raise ValueError("Enter a valid 6-digit OTP")
        auth_data = await verify_supabase_otp(email, otp)
        return await complete_auth_for_verified_email(response, request, email, auth_data, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/auth/trusted-login")
async def handle_trusted_login(response: Response, request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        if not is_trusted_bypass_email(email):
            raise HTTPException(status_code=403, detail="Trusted login is not enabled for this email")
        auth_data = await mint_supabase_session_for_email(email)
        return await complete_auth_for_verified_email(response, request, email, auth_data, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/onboarding/step1")
async def handle_onboarding_step1(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Handle Step 1 of onboarding: collect personal info and terms acceptance."""
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        first_name = clean_required(body.get("first_name"), "First name")
        last_name = clean_required(body.get("last_name"), "Last name")
        customer_company_name = clean_required(
            body.get("customer_company_name") or body.get("company_name"),
            "Company name",
        )
        terms_accepted = body.get("terms_accepted")
        if not terms_accepted:
            raise ValueError("You must accept the Terms & Conditions")
        
        share_token = body.get("share_token")
        dry_run = is_dry_run_request(request, body)

        if not dry_run:
            db = get_admin_db()
            
            # Create/update customer record
            customer = await upsert_customer(
                db,
                {
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "companyName": customer_company_name,
                    "isVerified": True,
                    "touchLogin": True,
                },
            )
            await mark_customer_verified(db, email)
            full_name = f"{first_name} {last_name}".strip()
            await create_stripe_customer(db, customer["customerId"], email, full_name)

            # Provision sample site row for new user (non-fatal if it fails)
            try:
                await ensure_sample_site_row(db, customer["customerId"])
            except Exception as exc:
                print(f"Sample site row creation failed for {email}: {exc}")

            # Handle share token if present
            if share_token:
                share_payload = decode_share_token(share_token)
                if share_payload and share_payload["recipient_email"] == email:
                    # Check if already synced (existing user had it auto-created)
                    existing_assignment = await db.request(
                        "GET",
                        "/rest/v1/automatisor_customer_sites",
                        params={
                            "select": "customer_site_id,site_id,account_id,assigned_via,shared_by",
                            "customer_id": f"eq.{customer['customerId']}",
                            "site_id": f"eq.{share_payload['site_id']}",
                            "assigned_via": "eq.shared_site",
                            "shared_by": f"eq.{share_payload.get('shared_by')}",
                            "limit": 1,
                        },
                    )
                    
                    if existing_assignment:
                        # Already synced - use existing assignment
                        shared_assignment = existing_assignment[0]
                    elif share_payload.get("share_type") == "chat":
                        shared_assignment = await ensure_chat_shared_site_assignment(
                            db,
                            customer["customerId"],
                            share_payload["site_id"],
                            share_payload.get("shared_by") or "",
                        )
                    else:
                        # New user - create assignment now
                        shared_assignment = await ensure_shared_site_assignment(
                            db, customer["customerId"], share_payload["site_id"], share_payload.get("shared_by")
                        )

                    await fulfill_pending_chat_shares(db, email, customer["customerId"])
                    
                    workspace = await build_workspace_payload(db, email)
                    return {
                        "status": "step1_complete",
                        "next_step": "share_destination",
                        **workspace,
                        "share_destination": {
                            "customer_site_id": shared_assignment["customer_site_id"],
                            "site_id": shared_assignment["site_id"],
                            "account_id": shared_assignment["account_id"],
                        },
                    }
            
            # Normal flow: proceed to step 2
            workspace = await build_workspace_payload(db, email)
            return {
                "status": "step1_complete",
                "next_step": "onboarding_step2",
                **workspace,
            }

        # Dry run response
        return {
            "status": "step1_complete",
            "email": email,
            "user_mode": "new_user",
            "next_step": "onboarding_step2",
            "customer_id": None,
            "account_id": None,
            "company_name": customer_company_name,
            "company_domain": "",
            "credits_used_total": 0,
            "credits_used_this_month": 0,
            "dry_run": dry_run,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/onboarding/complete")
async def handle_complete_onboarding(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Handle Step 2 of onboarding: site addition and pre-assessment setup.
    Note: Customer must already exist from Step 1."""
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        dry_run = is_dry_run_request(request, body)

        site_result = {"status": "pending_confirmation", "siteId": None}

        if not dry_run:
            db = get_admin_db()
            
            # Customer should already exist from Step 1
            customer = await find_customer_by_email(db, email)
            if not customer:
                raise HTTPException(status_code=400, detail="Customer record not found. Please complete Step 1 first.")
            
            # Site addition will be handled via the existing NewSitePage flow
            # This endpoint just confirms completion and returns workspace state
            workspace = await build_workspace_payload(db, email)
            return {
                "status": "onboarding_complete",
                **workspace,
                "site_status": site_result["status"],
                "site_id": site_result["siteId"],
                "dry_run": dry_run,
            }

        return {
            "status": "onboarding_complete",
            "email": email,
            "user_mode": "existing_user",
            "next_step": "workspace",
            "customer_id": None,
            "account_id": None,
            "company_name": "",
            "company_domain": "",
            "credits_used_total": 0,
            "credits_used_this_month": 0,
            "site_status": site_result["status"],
            "site_id": site_result["siteId"],
            "dry_run": dry_run,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/account-sites")
async def handle_add_account_site(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        site = parse_site_input(body)
        selected_account_id = clean_optional(body.get("account_id"))
        customer_site_validation = compact_validation_evidence(body, include_justification=True)
        dry_run = is_dry_run_request(request, body)

        account = None
        customer = {"customerId": None}
        site_result = {"status": "created", "siteId": None}

        if not dry_run:
            db = get_admin_db()
            existing_customer = await find_customer_by_email(db, email)
            if not existing_customer:
                raise HTTPException(status_code=422, detail="Customer not found")

            # Gate: second site onwards requires a payment method on file
            if not existing_customer.get("payment_method_id"):
                existing_sites = await db.request(
                    "GET",
                    "/rest/v1/automatisor_customer_sites",
                    params={
                        "select": "site_id",
                        "customer_id": f"eq.{existing_customer['customer_id']}",
                        "assigned_via": "not.in.(shared_site,sample_site)",
                        "limit": 1,
                    },
                )
                if existing_sites:
                    raise HTTPException(
                        status_code=402,
                        detail={
                            "code": "payment_method_required",
                            "message": "Please add a payment method before adding more sites.",
                        },
                    )

            if selected_account_id:
                account = await find_account_by_id(db, selected_account_id)
                if not account:
                    raise HTTPException(status_code=422, detail="Selected account was not found")
                account = {
                    "accountId": account["account_id"],
                    "companyName": account.get("company_name") or "",
                    "domain": account.get("account_domain") or "",
                }
            else:
                org_name = clean_required(body.get("org_name"), "Company name")
                domain = normalize_domain(body.get("org_domain") or body.get("company_domain"))
                account = await upsert_account(db, org_name, domain)

            if not site["fullAddress"]:
                raise HTTPException(status_code=422, detail="Site address is required")

            customer = await upsert_customer(
                db,
                {
                    "email": email,
                    "firstName": existing_customer.get("first_name") or "",
                    "lastName": existing_customer.get("last_name") or "",
                    "designation": existing_customer.get("designation") or "",
                    "companyName": existing_customer.get("company_name") or "",
                    "companyDomain": existing_customer.get("company_domain") or "",
                    "isVerified": bool(existing_customer.get("email_verified")),
                },
            )
            site_result = await insert_account_site_if_missing(
                db,
                account["accountId"],
                account["companyName"],
                site,
                customer["customerId"],
                customer_site_validation=customer_site_validation,
            )
            workspace = await build_workspace_payload(db, email, account["accountId"])
            return {
                "status": "site_saved",
                **workspace,
                "site_status": site_result["status"],
                "site_id": site_result["siteId"],
                "customer_site_id": site_result.get("customerSiteId"),
                "can_proceed": site_result.get("can_proceed", True),
                "site_reason": site_result.get("reason"),
                "dry_run": dry_run,
            }

        fallback_org_name = "" if selected_account_id else clean_optional(body.get("org_name"))
        fallback_domain = "" if selected_account_id else clean_optional(body.get("org_domain") or body.get("company_domain"))
        account = {"accountId": selected_account_id or None, "companyName": fallback_org_name, "domain": fallback_domain}
        return {
            "status": "site_saved",
            "email": email,
            "account_id": account["accountId"],
            "active_account_id": account["accountId"],
            "company_name": account["companyName"],
            "company_domain": account["domain"],
            "credits_used_total": 0,
            "credits_used_this_month": 0,
            "site_status": site_result["status"],
            "site_id": site_result["siteId"],
            "customer_site_id": site_result.get("customerSiteId"),
            "can_proceed": site_result.get("can_proceed", True),
            "site_reason": site_result.get("reason"),
            "dry_run": dry_run,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workspace/state")
async def workspace_state(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        db = get_admin_db()
        expected_email = clean_optional(body.get("email") or body.get("work_email")) or None
        customer = await get_authenticated_customer(db, request, expected_email=expected_email)
        requested_account_id = clean_optional(body.get("active_account_id"))
        return await build_workspace_payload(db, customer["email"], requested_account_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/customer-sites/notes")
async def save_customer_site_notes(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        account_id = clean_required(body.get("account_id"), "Account")
        site_id = clean_required(body.get("site_id"), "Site")
        notes = str(body.get("notes") or "")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        assignment_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "select": "customer_site_id",
                "customer_id": f"eq.{customer['customer_id']}",
                "account_id": f"eq.{account_id}",
                "site_id": f"eq.{site_id}",
                "limit": 1,
            },
        )
        assignment = assignment_rows[0] if assignment_rows else None
        if not assignment:
            raise HTTPException(status_code=422, detail="Selected site was not found for this customer")
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_sites",
            params={"customer_site_id": f"eq.{assignment['customer_site_id']}"},
            json_body={
                "notes": notes,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )
        return {
            "status": "saved",
            "account_id": account_id,
            "site_id": site_id,
            "notes": notes,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/customer-sites/rating")
async def save_customer_site_rating(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        account_id = clean_required(body.get("account_id"), "Account")
        site_id = clean_required(body.get("site_id"), "Site")
        rating_metadata = build_rating_metadata(body)
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        assignment_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "select": "customer_site_id",
                "customer_id": f"eq.{customer['customer_id']}",
                "account_id": f"eq.{account_id}",
                "site_id": f"eq.{site_id}",
                "limit": 1,
            },
        )
        assignment = assignment_rows[0] if assignment_rows else None
        if not assignment:
            raise HTTPException(status_code=422, detail="Selected site was not found for this customer")
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_sites",
            params={"customer_site_id": f"eq.{assignment['customer_site_id']}"},
            json_body={
                "rating_metadata": rating_metadata,
                "updated_at": rating_metadata["updated_at"],
            },
            headers={"Prefer": "return=minimal"},
        )
        return {
            "status": "saved",
            "account_id": account_id,
            "site_id": site_id,
            "rating_metadata": rating_metadata,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/customer-context/wishlist")
async def add_customer_context_wishlist(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        account_id = clean_required(body.get("account_id"), "Account")
        site_id = clean_required(body.get("site_id"), "Site")
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        notes = await resolve_wishlist_notes_from_discovery(
            db,
            customer["customer_id"],
            site_id,
            metadata,
            str(body.get("notes") or ""),
        )
        item = await add_customer_wishlist_item(
            db,
            customer["customer_id"],
            account_id,
            site_id,
            metadata=metadata,
            notes=notes,
        )
        return {
            "status": "saved",
            "wishlist_item": item,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/customer-context/wishlist/notes")
async def save_customer_context_wishlist_notes(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        site_id = clean_required(body.get("site_id"), "Site")
        notes = str(body.get("notes") or "")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_context",
            params={
                "select": CUSTOMER_CONTEXT_SELECT,
                "customer_id": f"eq.{customer['customer_id']}",
                "site_id": f"eq.{site_id}",
                "event_type": f"eq.{EVENT_TYPE_WISH_LIST}",
                "limit": 1,
            },
        )
        row = rows[0] if rows else None
        if not row:
            raise HTTPException(status_code=422, detail="Wishlist item not found")
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_context",
            params={"customer_context_id": f"eq.{row['customer_context_id']}"},
            json_body={
                "notes": notes,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )
        site_rows = await db.request(
            "GET",
            "/rest/v1/account_sites",
            params={
                "select": "site_id,account_id,full_address,company_name,metadata",
                "site_id": f"eq.{site_id}",
                "is_archived": "eq.false",
                "limit": 1,
            },
        )
        site = site_rows[0] if site_rows else None
        if not site:
            raise HTTPException(status_code=422, detail="Selected wishlist site was not found")
        account_rows = await db.request(
            "GET",
            "/rest/v1/accounts",
            params={
                "select": "account_id,company_name,account_domain",
                "account_id": f"eq.{site.get('account_id')}",
                "limit": 1,
            },
        )
        account = account_rows[0] if account_rows else {}
        updated_row = {**row, "notes": notes}
        return {
            "status": "saved",
            "site_id": site_id,
            "notes": notes,
            "wishlist_item": _hydrate_wishlist_item_row(updated_row, site, account),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies")
async def save_company(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        org_name = clean_required(body.get("org_name") or body.get("company_name"), "Company name")
        domain = normalize_domain(body.get("org_domain") or body.get("company_domain"))
        if not domain:
            raise ValueError("Company domain is required")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        company = await save_customer_company(db, customer["customer_id"], org_name, domain)
        return {"status": "saved", "company": company}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies/bulk")
async def save_companies_bulk(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        raw_items = body.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("items is required")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        results: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                results.append({"status": "failed", "error": "Invalid item"})
                continue
            org_name = clean_optional(item.get("org_name") or item.get("company_name"))
            domain = normalize_domain(item.get("org_domain") or item.get("company_domain"))
            if not org_name and not domain:
                continue
            try:
                org_name = clean_required(org_name, "Company name")
                if not domain:
                    raise ValueError("Company domain is required")
                company = await save_customer_company(db, customer["customer_id"], org_name, domain)
                results.append({"status": "ok", "company": company})
            except ValueError as exc:
                results.append(
                    {
                        "status": "failed",
                        "org_name": org_name or "",
                        "org_domain": domain or "",
                        "error": str(exc),
                    }
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                results.append(
                    {
                        "status": "failed",
                        "org_name": org_name or "",
                        "org_domain": domain or "",
                        "error": detail,
                        "error_code": exc.status_code,
                    }
                )
        if not results:
            raise ValueError("Add at least one company with a name and domain")
        return {"status": "ok", "results": results}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies/list")
async def list_companies(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        companies = await list_customer_companies(db, customer["customer_id"])
        return {"status": "ok", "companies": companies}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies/notes")
async def save_customer_company_notes(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        customer_context_id = clean_required(body.get("customer_context_id"), "Company")
        notes = str(body.get("notes") or "")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        company = await find_customer_company_by_id(db, customer["customer_id"], customer_context_id)
        if not company:
            raise HTTPException(status_code=422, detail="Company not found")
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_context",
            params={"customer_context_id": f"eq.{customer_context_id}"},
            json_body={
                "notes": notes,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )
        updated = await find_customer_company_by_id(db, customer["customer_id"], customer_context_id)
        return {
            "status": "saved",
            "customer_context_id": customer_context_id,
            "notes": notes,
            "company": updated,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies/site-note")
async def save_company_site_note(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        customer_context_id = clean_required(body.get("customer_context_id"), "Company")
        site_id = clean_required(body.get("site_id"), "Site")
        note = str(body.get("note") or "")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_context",
            params={
                "select": "customer_context_id,customer_id,metadata",
                "customer_context_id": f"eq.{customer_context_id}",
                "customer_id": f"eq.{customer['customer_id']}",
                "limit": 1,
            },
        )
        row = rows[0] if rows else None
        if not row:
            raise HTTPException(status_code=422, detail="Company not found")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        discovery = metadata.get("discovery") if isinstance(metadata.get("discovery"), dict) else {}
        company_sites = discovery.get("company_sites") if isinstance(discovery.get("company_sites"), list) else []
        found = False
        for item in company_sites:
            if isinstance(item, dict) and clean_optional(item.get("site_id")) == site_id:
                item["note"] = note
                found = True
                break
        if not found:
            raise HTTPException(status_code=422, detail="Site not found in discovery results")
        discovery["company_sites"] = company_sites
        metadata["discovery"] = discovery
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_customer_context",
            params={"customer_context_id": f"eq.{customer_context_id}"},
            json_body={
                "metadata": metadata,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            headers={"Prefer": "return=minimal"},
        )
        updated = await find_customer_company_by_id(db, customer["customer_id"], customer_context_id)
        return {
            "status": "saved",
            "customer_context_id": customer_context_id,
            "site_id": site_id,
            "note": note,
            "company": updated,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies/discover")
async def discover_company_facilities(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        customer_context_id = clean_required(body.get("customer_context_id"), "Company")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        if is_dry_run_request(request, body):
            company = await find_customer_company_by_id(db, customer["customer_id"], customer_context_id)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found.")
            return {
                "status": "running",
                "company": company,
                "message": "Dry run: facility discovery would start.",
            }
        enqueued = await _enqueue_company_discovery_on_worker(
            [customer_context_id],
            send_email=False,
        )
        if not enqueued:
            raise HTTPException(
                status_code=503,
                detail="Facility discovery service is unavailable. Please try again shortly.",
            )
        company = await trigger_company_discovery(db, customer["customer_id"], customer_context_id)
        return {
            "status": "running",
            "company": company,
            "message": "Facility discovery is running. We'll email you when the sites are ready.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/companies/discover/bulk")
async def discover_company_facilities_bulk(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        raw_ids = body.get("customer_context_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("customer_context_ids is required")
        customer_context_ids = [clean_required(item, "Company") for item in raw_ids]
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        eligible_ids: list[str] = []
        for customer_context_id in customer_context_ids:
            company = await find_customer_company_by_id(db, customer["customer_id"], customer_context_id)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found.")
            status = clean_optional((company.get("discovery") or {}).get("status")) or "idle"
            if status in {"running", "review", "ready"}:
                raise HTTPException(
                    status_code=422,
                    detail=f"Facility discovery cannot start for {customer_context_id} with status {status}.",
                )
            eligible_ids.append(customer_context_id)

        if eligible_ids:
            enqueued = await _enqueue_company_discovery_on_worker(eligible_ids, send_email=False)
            if not enqueued:
                raise HTTPException(
                    status_code=503,
                    detail="Facility discovery service is unavailable. Please try again shortly.",
                )

        results: list[dict[str, Any]] = []
        for customer_context_id in customer_context_ids:
            try:
                company = await trigger_company_discovery(db, customer["customer_id"], customer_context_id)
                results.append(
                    {
                        "customer_context_id": customer_context_id,
                        "status": "running",
                        "company": company,
                    }
                )
            except HTTPException as exc:
                results.append(
                    {
                        "customer_context_id": customer_context_id,
                        "status": "failed",
                        "error": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                    }
                )
        return {"status": "ok", "results": results}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/customer-context/user")
async def get_customer_context_user(request: Request) -> dict[str, Any]:
    db = get_admin_db()
    customer = await get_authenticated_customer(db, request)
    row = await get_customer_user_context(db, customer["customer_id"])
    metadata = row.get("metadata") if isinstance(row, dict) and isinstance(row.get("metadata"), dict) else {}
    return {
        "status": "ok",
        "customer_context": row,
        "context": str(metadata.get("context") or ""),
    }


@app.post("/api/customer-context/user")
async def save_customer_context_user(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    account_id = clean_required(body.get("account_id"), "Account")
    context_text = str(body.get("context") or "").strip()
    if len(context_text) > 8000:
        raise HTTPException(status_code=422, detail="Context must be 8000 characters or fewer")
    db = get_admin_db()
    customer = await get_authenticated_customer(db, request)
    account = await find_account_by_id(db, account_id)
    if not account:
        raise HTTPException(status_code=422, detail="Account not found")
    row = await upsert_customer_user_context(db, customer["customer_id"], account_id, context_text)
    return {
        "status": "saved",
        "customer_context": row,
        "context": context_text,
    }


@app.get("/api/sample-site")
async def get_sample_site(request: Request) -> dict[str, Any]:
    """Return the current user's sample site row, creating it if absent."""
    db = get_admin_db()
    customer = await get_authenticated_customer(db, request)
    row = await ensure_sample_site_row(db, customer["customer_id"])
    return {"site_id": SAMPLE_SITE_ID, "customer_site_id": row["customer_site_id"]}


@app.post("/api/share/resolve")
async def resolve_share_token(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Resolve share token to validate and get site details."""
    payload = decode_share_token(body.get("share_token"))
    if not payload:
        raise HTTPException(status_code=422, detail="Invalid share link.")
    db = get_admin_db()
    sites = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,company_name,full_address",
            "site_id": f"eq.{payload['site_id']}",
            "is_archived": "eq.false",
            "limit": 1,
        },
    )
    if not sites:
        raise HTTPException(status_code=404, detail="The shared facility is no longer available.")
    site = sites[0]
    return {
        "status": "valid",
        "recipient_email": payload["recipient_email"],
        "site_id": payload["site_id"],
        "company_name": site.get("company_name") or "",
        "full_address": site.get("full_address") or "",
        "share_type": payload.get("share_type") or "report",
        "session_id": payload.get("session_id") or "",
    }


@app.post("/api/reports/share")
async def share_report(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Share a report with work email recipients."""
    try:
        customer_site_id = clean_optional(body.get("customer_site_id"))
        site_id = clean_optional(body.get("site_id"))
        if not customer_site_id and not site_id:
            raise ValueError("Report is required")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db = get_admin_db()
    sender = await get_authenticated_customer(db, request)
    assignment = await resolve_sender_report_assignment(
        db,
        sender["customer_id"],
        customer_site_id=customer_site_id or None,
        site_id=site_id or None,
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Report not found.")
    if assignment.get("assigned_via") == "sample_site":
        raise HTTPException(status_code=422, detail="The sample report cannot be shared.")
    if not assignment.get("is_report_ready"):
        raise HTTPException(status_code=422, detail="Only ready reports can be shared.")
    recipients, recipient_errors = validate_share_recipients(body.get("emails"), sender["email"])
    if recipient_errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "Correct the rejected email addresses before sending.", "recipient_errors": recipient_errors},
        )
    app_base_url = get_app_base_url(request)
    if not app_base_url:
        raise HTTPException(status_code=500, detail="Missing APP_BASE_URL")

    # AUTO-SYNC: Create shared assignments immediately for existing users
    for recipient_email in recipients:
        recipient_customer = await find_customer_by_email(db, recipient_email)
        if recipient_customer:
            # User exists - create shared assignment immediately
            try:
                await ensure_shared_site_assignment(
                    db,
                    recipient_customer["customer_id"],
                    assignment["site_id"],
                    sender["customer_id"],
                    source_customer_site_id=assignment["customer_site_id"],
                )
            except HTTPException as exc:
                print(
                    f"Auto-sync shared assignment failed for {recipient_email}: "
                    f"{exc.status_code} {exc.detail}"
                )
            except Exception as exc:
                print(f"Auto-sync shared assignment failed for {recipient_email}: {exc}")
    
    site_rows = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,company_name,full_address",
            "site_id": f"eq.{assignment['site_id']}",
            "limit": 1,
        },
    )
    site = site_rows[0] if site_rows else {}
    semaphore = asyncio.Semaphore(3)  # Resend free tier: max 3 concurrent

    async def send_one(recipient: str) -> dict[str, str]:
        token = encode_share_token(recipient, assignment["site_id"], sender["customer_id"])
        share_url = f"{app_base_url}/auth?share={token}"
        try:
            async with semaphore:
                await send_report_share_email(
                    recipient,
                    sender["email"],
                    site.get("company_name") or "",
                    site.get("full_address") or "",
                    share_url,
                )
            return {"email": recipient, "status": "sent"}
        except Exception as exc:
            error_msg = str(exc)
            if "rate limit" in error_msg.lower() or "429" in error_msg:
                return {"email": recipient, "status": "failed", "message": "Rate limit reached, try again later"}
            return {"email": recipient, "status": "failed", "message": error_msg}

    results = await asyncio.gather(*(send_one(recipient) for recipient in recipients))

    return {
        "status": "complete",
        "sent": sum(r["status"] == "sent" for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
        "results": results,
    }


@app.post("/api/auth/refresh")
async def refresh_session(response: Response, request: Request) -> dict[str, Any]:
    token = clean_optional(request.cookies.get("refresh_token"))
    if not token:
        raise HTTPException(status_code=401, detail="No active session.")
    auth_data = await refresh_supabase_session(token)
    session = auth_data.get("session") or {}
    if not session.get("access_token"):
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    set_auth_cookies(response, session)
    return {"status": "refreshed"}


@app.post("/api/auth/logout")
async def auth_logout(response: Response) -> dict[str, str]:
    clear_auth_cookies(response)
    return {"status": "logged_out"}


@app.post("/api/pre-assessment/request")
async def request_pre_assessment(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        requested_account_id = clean_optional(body.get("account_id"))
        requested_customer_site_id = clean_optional(body.get("customer_site_id"))
        site_id = clean_required(body.get("site_id"), "Site")
        confirmed = bool(body.get("confirmed"))
        if not confirmed:
            raise ValueError("Confirmation is required")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        linked_accounts = await list_customer_accounts(db, customer)
        active_account = choose_active_account(linked_accounts, requested_account_id)
        if requested_account_id and not active_account:
            raise HTTPException(status_code=422, detail="Selected account is not available for this user")
        resolved_account_id = requested_account_id or (active_account["account_id"] if active_account else "")
        result = await _submit_pre_assessment_request(
            db,
            request=request,
            body=body,
            customer=customer,
            email=email,
            account_id=resolved_account_id,
            site_id=site_id,
            customer_site_id=requested_customer_site_id,
        )
        return {
            **result,
            "email": email,
            "pre_assessment_price_credits": result.get("pre_assessment_price_credits", PRE_ASSESSMENT_PRICE),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _submit_pre_assessment_request(
    db: SupabaseAdmin,
    *,
    request: Request,
    body: dict[str, Any],
    customer: dict[str, Any],
    email: str,
    account_id: str,
    site_id: str,
    customer_site_id: str | None = None,
) -> dict[str, Any]:
    site = await find_account_site_by_id(
        db,
        account_id,
        site_id,
        customer["customer_id"],
        customer_site_id=customer_site_id,
    )
    if not site:
        raise HTTPException(status_code=422, detail="Selected site was not found for this customer")
    wishlist_notes = await get_wishlist_notes_for_site(db, customer["customer_id"], site_id)
    assignment_metadata = site.get("customer_site_metadata") or {}
    already_requested = bool(assignment_metadata.get("last_pre_assessment_requested_at"))
    if not already_requested and not is_dry_run_request(request, body):
        prior_site_billing = await db.request(
            "GET",
            "/rest/v1/automatisor_billing",
            params={
                "select": "billing_id",
                "customer_id": f"eq.{customer['customer_id']}",
                "site_id": f"eq.{site_id}",
                "usage_type": "eq.pre_assessment_request",
                "limit": 1,
            },
        )
        already_requested = bool(prior_site_billing)
    requested_at = datetime.now(timezone.utc).isoformat()
    if not is_dry_run_request(request, body):
        if already_requested:
            existing_customer_site_id = clean_optional(site.get("customer_site_id"))
            if existing_customer_site_id:
                await maybe_start_site_recommendations(
                    db,
                    site,
                    assignment_customer_site_id=existing_customer_site_id,
                )
                await apply_wishlist_notes_to_customer_site(db, existing_customer_site_id, wishlist_notes)
            await clear_wishlist_after_pre_assessment(
                db,
                customer["customer_id"],
                site_id,
                request=request,
                body=body,
            )
            usage = await get_customer_usage_state(db, customer["customer_id"])
            return {
                "status": "running",
                "account_id": site["account_id"],
                "site_id": site["site_id"],
                "customer_site_id": site.get("customer_site_id"),
                "message": "Pre-assessment is already running for this site.",
                "credits_used_total": usage["creditsUsedTotal"],
                "credits_used_this_month": usage["creditsUsedThisMonth"],
            }
        stripe_customer_id = customer.get("stripe_customer_id")
        prior_billing_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_billing",
            params={"select": "billing_id", "customer_id": f"eq.{customer['customer_id']}", "limit": 1},
        )
        is_first_report = not bool(prior_billing_rows)
        if stripe_customer_id and not is_first_report:
            stripe_cust = stripe.Customer.retrieve(
                stripe_customer_id,
                expand=["invoice_settings.default_payment_method"],
            )
            has_default_pm = bool(
                getattr(stripe_cust, "invoice_settings", None)
                and getattr(stripe_cust.invoice_settings, "default_payment_method", None)
            )
            if not has_default_pm:
                attached = stripe.PaymentMethod.list(customer=stripe_customer_id, type="card", limit=1)
                has_default_pm = bool(attached.data)
            if not has_default_pm:
                raise HTTPException(
                    status_code=402,
                    detail="Add a payment method to continue using credits. You won't be charged until usage is billed.",
                )
            open_invoices = stripe.Invoice.list(customer=stripe_customer_id, status="open", limit=1)
            if open_invoices.data:
                raise HTTPException(
                    status_code=402,
                    detail="Your account has an unpaid invoice. Please pay your outstanding balance to continue using the service.",
                )
        assignment = await ensure_customer_site_assignment(
            db,
            customer["customer_id"],
            site["account_id"],
            site["site_id"],
            requested_at=requested_at,
        )
        await apply_wishlist_notes_to_customer_site(db, assignment.get("customerSiteId"), wishlist_notes)
        await maybe_start_site_recommendations(
            db,
            site,
            assignment_customer_site_id=assignment.get("customerSiteId"),
        )
        await insert_billing_usage(
            db,
            customer_id=customer["customer_id"],
            account_id=site["account_id"],
            site_id=site["site_id"],
            usage_type="pre_assessment_request",
            credits_used=PRE_ASSESSMENT_PRICE,
            metadata={"requested_at": requested_at},
        )
        try:
            await send_pre_assessment_approval_email(
                email,
                site.get("company_name") or customer.get("company_name") or "",
                site.get("full_address") or "",
            )
        except Exception as email_exc:
            print(f"Pre-assessment approval email failed: {email_exc}")
        try:
            contact_name = " ".join(
                part for part in [customer.get("first_name") or "", customer.get("last_name") or ""] if part
            ).strip()
            await send_slack_pre_assessment_notification(
                company_name=site.get("company_name") or customer.get("company_name") or "",
                site_address=site.get("full_address") or "",
                contact_name=contact_name,
                email=email,
                designation=customer.get("designation") or "",
                account_id=site.get("account_id"),
                site_id=site.get("site_id"),
                customer_site_id=site.get("customer_site_id"),
            )
        except Exception as slack_exc:
            print(f"Slack notification failed: {slack_exc}")
    await clear_wishlist_after_pre_assessment(
        db,
        customer["customer_id"],
        site_id,
        request=request,
        body=body,
    )
    usage = await get_customer_usage_state(db, customer["customer_id"])
    return {
        "status": "running",
        "account_id": site["account_id"],
        "site_id": site["site_id"],
        "customer_site_id": site.get("customer_site_id"),
        "credits_used_total": usage["creditsUsedTotal"],
        "credits_used_this_month": usage["creditsUsedThisMonth"],
        "pre_assessment_price_credits": PRE_ASSESSMENT_PRICE,
        "message": "Pre-assessment request approved.",
    }


@app.post("/api/pre-assessment/request/bulk")
async def request_pre_assessment_bulk(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        confirmed = bool(body.get("confirmed"))
        if not confirmed:
            raise ValueError("Confirmation is required")
        raw_items = body.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("items is required")
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            raise HTTPException(status_code=422, detail="Customer not found")
        results: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                results.append({"status": "failed", "error": "Invalid item"})
                continue
            account_id = clean_optional(item.get("account_id"))
            site_id = clean_optional(item.get("site_id"))
            customer_site_id = clean_optional(item.get("customer_site_id")) or None
            if not account_id or not site_id:
                results.append({"status": "failed", "error": "account_id and site_id are required", "site_id": site_id})
                continue
            try:
                result = await _submit_pre_assessment_request(
                    db,
                    request=request,
                    body=body,
                    customer=customer,
                    email=email,
                    account_id=account_id,
                    site_id=site_id,
                    customer_site_id=customer_site_id,
                )
                results.append({"status": "ok", **result})
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                results.append(
                    {
                        "status": "failed",
                        "account_id": account_id,
                        "site_id": site_id,
                        "error": detail,
                        "error_code": exc.status_code,
                    }
                )
        usage = await get_customer_usage_state(db, customer["customer_id"])
        return {
            "status": "ok",
            "results": results,
            "credits_used_total": usage["creditsUsedTotal"],
            "credits_used_this_month": usage["creditsUsedThisMonth"],
            "pre_assessment_price_credits": PRE_ASSESSMENT_PRICE,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/credits/usage")
async def get_credits_usage(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required.")
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    billing_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_billing",
        params={
            "select": "site_id,usage_type,credits_used,created_at,is_free",
            "customer_id": f"eq.{customer['customer_id']}",
            "order": "created_at.desc",
        },
    )
    billing_rows = billing_rows or []

    site_ids = list({row["site_id"] for row in billing_rows if row.get("site_id")})
    site_map: dict[str, dict[str, str]] = {}
    if site_ids:
        site_rows = await db.request(
            "GET",
            "/rest/v1/account_sites",
            params={
                "select": "site_id,company_name,full_address",
                "site_id": f"in.({','.join(site_ids)})",
            },
        )
        for row in site_rows or []:
            site_map[row["site_id"]] = {
                "company_name": row.get("company_name") or "",
                "full_address": row.get("full_address") or "",
            }

    # Anchor = when the card was added (billing_period_start). Fall back to earliest billing row or now.
    anchor: datetime | None = None
    raw_anchor = customer.get("billing_period_start")
    if raw_anchor:
        try:
            anchor = datetime.fromisoformat(str(raw_anchor).replace("Z", "+00:00"))
        except ValueError:
            anchor = None
    if anchor is None and billing_rows:
        oldest_raw = min((r.get("created_at") or "" for r in billing_rows), default="")
        try:
            anchor = datetime.fromisoformat(str(oldest_raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    if anchor is None:
        anchor = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)

    def period_index_for(ts: datetime) -> int:
        delta_days = (ts - anchor).days
        return max(delta_days // 30, 0)

    def period_bounds(index: int) -> tuple[datetime, datetime]:
        return (
            anchor + timedelta(days=index * 30),
            anchor + timedelta(days=(index + 1) * 30),
        )

    current_index = period_index_for(now)
    periods: dict[int, list[dict[str, Any]]] = {}
    for row in billing_rows:
        raw_ts = row.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        idx = period_index_for(ts)
        if idx not in periods:
            periods[idx] = []
        site_id = row.get("site_id") or ""
        site_info = site_map.get(site_id, {})
        usage_type = row.get("usage_type") or ""
        task_label = USAGE_TYPE_LABELS.get(usage_type, usage_type.replace("_", " ").title())
        periods[idx].append({
            "site_name": site_info.get("company_name") or "—",
            "site_address": site_info.get("full_address") or "—",
            "task_label": task_label,
            "timestamp_utc": raw_ts,
            "credits_used": int(row.get("credits_used") or 0),
            "is_free": bool(row.get("is_free")),
        })

    if current_index not in periods:
        periods[current_index] = []

    result_periods = []
    for idx in sorted(periods.keys(), reverse=True):
        start, end = period_bounds(idx)
        rows = periods[idx]
        result_periods.append({
            "period_index": str(idx),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "is_current": idx == current_index,
            "total_credits_used": sum(r["credits_used"] for r in rows),
            "rows": rows,
        })

    return {
        "billing_anchor_date": anchor.isoformat(),
        "billing_periods": result_periods,
    }


@app.post("/api/stripe/setup-intent")
async def create_setup_intent(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    db = get_admin_db()
    customer = await get_authenticated_customer(
        db,
        request,
        expected_email=body.get("email") or body.get("work_email"),
    )
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="Stripe customer not initialised. Complete onboarding first.")
    try:
        intent = stripe.SetupIntent.create(
            customer=stripe_customer_id,
            usage="off_session",
            payment_method_types=["card"],
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to start card setup: {exc}") from exc
    return {"client_secret": intent.client_secret}


@app.post("/api/stripe/confirm-payment-method")
async def confirm_payment_method(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    payment_method_id = (body.get("payment_method_id") or "").strip()
    if not payment_method_id:
        raise HTTPException(status_code=422, detail="payment_method_id is required.")
    db = get_admin_db()
    customer = await get_authenticated_customer(
        db,
        request,
        expected_email=body.get("email") or body.get("work_email"),
    )
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="Stripe customer not initialised.")
    try:
        stripe.Customer.modify(
            stripe_customer_id,
            invoice_settings={"default_payment_method": payment_method_id},
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to save card as default payment method: {exc}") from exc
    # Build the DB patch — always update payment_method_id.
    patch: dict[str, Any] = {"payment_method_id": payment_method_id}

    # Initialize rolling 30-day billing period only if not yet set.
    if not customer.get("billing_period_start") or not customer.get("billing_period_end"):
        now = datetime.now(timezone.utc)
        patch["billing_period_start"] = now.isoformat()
        patch["billing_period_end"] = (now + timedelta(days=30)).isoformat()

    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer['customer_id']}"},
        json_body=patch,
        headers={"Prefer": "return=minimal"},
    )
    return {"ok": True}


@app.post("/api/billing/invoices")
async def get_billing_invoices(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    db = get_admin_db()
    customer = await get_authenticated_customer(
        db,
        request,
        expected_email=body.get("email") or body.get("work_email"),
    )
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        return {"invoices": [], "payment_method": None}
    # Fetch default payment method info
    stripe_customer = stripe.Customer.retrieve(
        stripe_customer_id,
        expand=["invoice_settings.default_payment_method"],
    )
    pm = stripe_customer.invoice_settings.default_payment_method
    payment_method_info = None
    if pm and hasattr(pm, "card") and pm.card:
        payment_method_info = {
            "brand":     pm.card.brand,
            "last4":     pm.card.last4,
            "exp_month": pm.card.exp_month,
            "exp_year":  pm.card.exp_year,
        }
    stripe_invoices = stripe.Invoice.list(customer=stripe_customer_id, limit=12)
    invoices = []
    for inv in stripe_invoices.auto_paging_iter():
        # Skip voided/written-off invoices — not actionable for the customer
        if inv.status in ("void", "uncollectible"):
            continue
        # Use amount_due for open/draft (what the customer still owes), amount_paid for settled
        amount_cents = inv.amount_due if inv.status in ("open", "draft") else inv.amount_paid
        invoices.append({
            "invoice_id":     inv.id,
            "invoice_number": inv.number,
            "invoice_date":   datetime.fromtimestamp(inv.created, tz=timezone.utc).isoformat(),
            "period_start":   datetime.fromtimestamp(inv.period_start, tz=timezone.utc).isoformat() if inv.period_start else None,
            "period_end":     datetime.fromtimestamp(inv.period_end, tz=timezone.utc).isoformat() if inv.period_end else None,
            "amount_usd":     amount_cents / 100,
            "status":         inv.status,
            "pdf_url":        inv.invoice_pdf,
            "payment_url":    inv.hosted_invoice_url,
        })
        if len(invoices) >= 12:
            break
    return {"invoices": invoices, "payment_method": payment_method_info}


@app.post("/api/stripe/portal-session")
async def create_portal_session(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return_url = (body.get("return_url") or "").strip()
    db = get_admin_db()
    customer = await get_authenticated_customer(
        db,
        request,
        expected_email=body.get("email") or body.get("work_email"),
    )
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="No Stripe customer found. Complete onboarding first.")
    portal_session = stripe.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=return_url or "https://automatisor.app/workspace/billing",
        configuration=None,  # uses default portal config — see note below
    )
    # NOTE: To hide the × remove button when it's the only card, go to
    # Stripe Dashboard → Billing → Customer portal → Payment methods
    # and enable "Require customers to always have a payment method on file".
    # This cannot be set per-session via the API; it's a portal configuration setting.
    return {"url": portal_session.url}


@app.post("/api/billing/pay-invoice")
async def pay_invoice_endpoint(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(status_code=422, detail="invoice_id is required.")
    db = get_admin_db()
    customer = await get_authenticated_customer(
        db,
        request,
        expected_email=body.get("email") or body.get("work_email"),
    )
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="No Stripe customer found.")
    # Verify the invoice belongs to this customer before charging
    inv = stripe.Invoice.retrieve(invoice_id)
    if inv.customer != stripe_customer_id:
        raise HTTPException(status_code=403, detail="Invoice does not belong to this account.")
    if inv.status not in ("open", "draft"):
        raise HTTPException(status_code=422, detail=f"Invoice is not payable (status: {inv.status}).")
    # Finalize first if still a draft
    if inv.status == "draft":
        inv = stripe.Invoice.finalize_invoice(invoice_id)
    paid_inv = stripe.Invoice.pay(inv.id)
    return {"ok": True, "status": paid_inv.status}


@app.post("/api/billing/get-invoice-url")
async def get_invoice_url(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Return the Stripe hosted payment URL for an invoice.
    Finalizes draft invoices (no charge) to obtain the URL.
    """
    invoice_id = (body.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(status_code=422, detail="invoice_id is required.")
    db = get_admin_db()
    customer = await get_authenticated_customer(
        db,
        request,
        expected_email=body.get("email") or body.get("work_email"),
    )
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="No Stripe customer found.")
    inv = stripe.Invoice.retrieve(invoice_id)
    if inv.customer != stripe_customer_id:
        raise HTTPException(status_code=403, detail="Invoice does not belong to this account.")
    if inv.status not in ("open", "draft"):
        raise HTTPException(status_code=422, detail=f"Invoice is not payable (status: {inv.status}).")
    # Finalize draft to generate the hosted_invoice_url (this does NOT charge the customer)
    if inv.status == "draft":
        inv = stripe.Invoice.finalize_invoice(invoice_id)
    url = inv.hosted_invoice_url
    if not url:
        raise HTTPException(status_code=500, detail="Stripe did not return a payment URL for this invoice.")
    return {"url": url}


@app.get("/api/cron/billing")
async def vercel_cron_billing(request: Request) -> dict[str, Any]:
    """Called daily by Vercel Cron at 01:00 UTC. Protected by CRON_SECRET."""
    cron_secret = os.getenv("CRON_SECRET", "")
    if not cron_secret:
        raise HTTPException(status_code=503, detail="CRON_SECRET is not configured.")
    if request.headers.get("authorization") != f"Bearer {cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized.")
    await run_billing_cron()
    return {"ok": True}


@app.post("/api/dev/trigger-billing-cron")
async def dev_trigger_billing_cron(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """DEV ONLY — manually fire the billing cron. Remove before production."""
    if os.getenv("ALLOW_DEV_TRIGGERS", "") != "1":
        raise HTTPException(status_code=403, detail="Dev triggers are disabled.")
    await run_billing_cron()
    return {"ok": True, "message": "Billing cron completed."}


@app.post("/api/dev/create-test-invoice")
async def dev_create_test_invoice(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """DEV ONLY — create a real open Stripe invoice for testing Pay Now. Remove before production."""
    if os.getenv("ALLOW_DEV_TRIGGERS", "") != "1":
        raise HTTPException(status_code=403, detail="Dev triggers are disabled.")
    email = (body.get("email") or "").strip().lower()
    credits = int(body.get("credits") or 2)
    if not email:
        raise HTTPException(status_code=422, detail="email is required.")
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    stripe_customer_id = customer.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=422, detail="No Stripe customer — add a card first.")
    amount_cents = credits * PRICE_PER_CREDIT_USD_CENTS
    # Create invoice first, then attach the item to it — avoids pending-item being swept to another invoice
    invoice = stripe.Invoice.create(
        customer=stripe_customer_id,
        auto_advance=False,  # keep as draft — prevents auto-charge during testing
        collection_method="charge_automatically",
    )
    stripe.InvoiceItem.create(
        customer=stripe_customer_id,
        invoice=invoice.id,
        amount=amount_cents,
        currency="usd",
        description=f"[TEST] Automatisor — {credits} credit{'s' if credits != 1 else ''} (dev invoice)",
    )
    # Finalize so Stripe assigns an invoice_number and hosted_invoice_url (does NOT charge the customer)
    finalized = stripe.Invoice.finalize_invoice(invoice.id)
    return {"ok": True, "invoice_id": finalized.id, "status": finalized.status, "amount_usd": finalized.amount_due / 100}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request) -> dict[str, Any]:
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event_type = event["type"]

    if event_type == "invoice.payment_succeeded":
        # Roll the billing window forward if it hasn't been already.
        # The cron does this before creating an invoice, so this handles the case
        # where an invoice was paid manually (e.g. via the billing UI) and the
        # billing period was never advanced.
        stripe_cust_id = event["data"]["object"].get("customer", "")
        if stripe_cust_id:
            db = get_admin_db()
            now_utc = datetime.now(timezone.utc)
            stale_customers = await db.request(
                "GET",
                "/rest/v1/automatisor_customer",
                params={
                    "select": "customer_id",
                    "stripe_customer_id": f"eq.{stripe_cust_id}",
                    "billing_period_end": f"lte.{now_utc.isoformat()}",
                },
            )
            if stale_customers:
                customer_id = stale_customers[0]["customer_id"]
                if now_utc.month == 12:
                    next_period_end = datetime(now_utc.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                else:
                    next_period_end = datetime(now_utc.year, now_utc.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                await db.request(
                    "PATCH",
                    "/rest/v1/automatisor_customer",
                    params={"customer_id": f"eq.{customer_id}"},
                    json_body={
                        "billing_period_start": now_utc.isoformat(),
                        "billing_period_end": next_period_end.isoformat(),
                    },
                    headers={"Prefer": "return=minimal"},
                )

    elif event_type == "invoice.payment_failed":
        pass  # Stripe retries automatically; extend here to notify customer if desired

    elif event_type == "setup_intent.succeeded":
        pass  # confirm-payment-method endpoint handles the DB write; this is a backup confirm

    elif event_type == "payment_method.detached":
        # A card was removed (e.g. via the Customer Portal). Clear it from our DB so the
        # billing cron does not attempt to charge a detached payment method.
        pm_id = event["data"]["object"].get("id", "")
        if pm_id:
            db = get_admin_db()
            await db.request(
                "PATCH",
                "/rest/v1/automatisor_customer",
                params={"payment_method_id": f"eq.{pm_id}"},
                json_body={"payment_method_id": None},
                headers={"Prefer": "return=minimal"},
            )

    return {"received": True}


@app.post("/api/signup/request-otp")
async def signup_request_alias(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await handle_request_otp(request, body)


@app.post("/api/signup/verify-otp")
async def signup_verify_alias(response: Response, request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await handle_verify_otp(response, request, body)


@app.post("/api/accounts/new-user")
async def accounts_new_user_alias(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await handle_request_otp(request, body)


# ── Billing cron ──────────────────────────────────────────────────────────────

async def run_billing_cron() -> None:
    """Triggered by the platform cron. Bills all customers whose billing period has ended."""
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
            print(f"[billing-cron] ERROR for {customer.get('email')}: {exc}")


async def _process_billing_period(db: SupabaseAdmin, customer: dict[str, Any], now: datetime) -> None:
    customer_id        = customer["customer_id"]
    stripe_customer_id = customer["stripe_customer_id"]
    period_start       = customer.get("billing_period_start")

    # Fetch usage rows for this period (exclude rows explicitly marked is_free=true)
    usage_params: dict[str, Any] = {
        "select": "billing_id,credits_used",
        "customer_id": f"eq.{customer_id}",
        "is_free": "not.is.true",
    }
    if period_start:
        usage_params["created_at"] = f"gte.{period_start}"
    usage_rows = await db.request("GET", "/rest/v1/automatisor_billing", params=usage_params)
    usage_rows = [r for r in (usage_rows or []) if r.get("billing_id")]
    total_credits = sum(int(r.get("credits_used") or 0) for r in usage_rows)
    row_ids = [r["billing_id"] for r in usage_rows]

    # Open next billing period
    if now.month == 12:
        next_period_end = datetime(now.year + 1, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    else:
        next_period_end = datetime(now.year, now.month + 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    await db.request(
        "PATCH",
        "/rest/v1/automatisor_customer",
        params={"customer_id": f"eq.{customer_id}"},
        json_body={
            "billing_period_start": now.isoformat(),
            "billing_period_end": next_period_end.isoformat(),
        },
        headers={"Prefer": "return=minimal"},
    )

    # Paid period — charge via Stripe
    if total_credits == 0:
        print(f"[billing-cron] Zero usage for {customer.get('email')} — no invoice created")
        return

    if not customer.get("payment_method_id"):
        print(f"[billing-cron] WARNING: {customer.get('email')} has no payment method — skipping charge")
        return

    amount_cents = total_credits * PRICE_PER_CREDIT_USD_CENTS
    cycle_identity = hashlib.sha256(
        "|".join(
            [
                str(customer_id),
                str(period_start or ""),
                str(customer.get("billing_period_end") or ""),
                str(total_credits),
                str(amount_cents),
            ]
        ).encode("utf-8")
    ).hexdigest()
    period_label = ""
    if period_start and customer.get("billing_period_end"):
        try:
            ps = datetime.fromisoformat(str(period_start).replace("Z", "+00:00"))
            pe = datetime.fromisoformat(str(customer["billing_period_end"]).replace("Z", "+00:00"))
            period_label = f"{ps.strftime('%b %d')} – {pe.strftime('%b %d, %Y')}"
        except ValueError:
            pass

    stripe.InvoiceItem.create(
        customer=stripe_customer_id,
        amount=amount_cents,
        currency="usd",
        description=f"Automatisor — {total_credits} credit{'s' if total_credits != 1 else ''} ({period_label})",
        idempotency_key=f"billing-item-{cycle_identity}",
    )
    invoice = stripe.Invoice.create(
        customer=stripe_customer_id,
        auto_advance=True,
        collection_method="charge_automatically",
        default_payment_method=customer["payment_method_id"],
        idempotency_key=f"billing-invoice-{cycle_identity}",
    )
    stripe.Invoice.pay(invoice.id, idempotency_key=f"billing-pay-{cycle_identity}")
    print(f"[billing-cron] Charged {customer.get('email')}: ${amount_cents / 100:.2f} ({total_credits} credits)")


def register_vercel_service_api_aliases() -> None:
    api_routes = [
        route
        for route in app.router.routes
        if (
            isinstance(route, APIRoute)
            and route.path.startswith("/api/")
            and not route.path.startswith("/api/sample-reports/")
        )
    ]
    existing = {
        (route.path, tuple(sorted(route.methods or [])))
        for route in app.router.routes
        if isinstance(route, APIRoute)
    }
    for route in api_routes:
        alias_path = route.path.removeprefix("/api")
        methods = tuple(sorted(route.methods or []))
        if (alias_path, methods) in existing:
            continue
        app.add_api_route(
            alias_path,
            route.endpoint,
            methods=list(methods),
            name=f"{route.name}_vercel_service_alias",
        )


register_vercel_service_api_aliases()


def dist_response_for_path(path: str) -> FileResponse | None:
    if not FRONTEND_DIST.exists():
        return None
    clean_path = path.lstrip("/") or "index.html"
    candidate = FRONTEND_DIST / clean_path
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(FRONTEND_DIST / "index.html")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str) -> Response:
    if full_path.startswith("api/"):
        return PlainTextResponse("Page not found", status_code=404)
    response = dist_response_for_path(full_path)
    if response:
        return response
    return PlainTextResponse("Frontend build not found. Run the React frontend build first.", status_code=503)
