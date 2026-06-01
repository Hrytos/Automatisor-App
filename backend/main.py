import os
import re
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone

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
except ImportError:
    from address_normalization import canonical_zip, normalize_full_address, normalize_state, normalize_street_line
    from address_validator import validate_company_site

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

PORT = int(os.getenv("PORT", "3000"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "no-reply@automatisor.com")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true") != "false"
SERVER_DRY_RUN = "--dry" in os.sys.argv or os.getenv("AUTOMATISOR_DRY") == "1"

SIGNUP_CREDITS = 1
PRE_ASSESSMENT_PRICE = 1
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

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
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVICE_API_PREFIXES = (
    "/account-sites",
    "/accounts",
    "/address-validation",
    "/billing",
    "/credits",
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
            or payload.get("error_description")
            or payload.get("error")
            or payload.get("details")
            or "Request failed"
        )
    return response.text or "Request failed"


def normalize_email(raw: Any) -> str:
    return str(raw or "").strip().lower()


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
            "select": "customer_id,email,first_name,last_name,full_name,designation,company_name,company_domain,email_verified,email_verified_at,last_login_at,metadata",
            "email": f"eq.{email}",
            "limit": 1,
        },
    )
    return data[0] if data else None


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


async def find_account_site_by_id(db: SupabaseAdmin, account_id: str | None, site_id: str | None, customer_id: str | None) -> dict[str, Any] | None:
    if not account_id or not site_id:
        return None
    assignment_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,metadata,notes,report_metadata,rating_metadata,is_report_ready",
            "customer_id": f"eq.{customer_id}",
            "account_id": f"eq.{account_id}",
            "site_id": f"eq.{site_id}",
            "limit": 1,
        },
    )
    assignment = assignment_rows[0] if assignment_rows else None
    if customer_id and not assignment:
        return None
    data = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,metadata,created_at",
            "account_id": f"eq.{account_id}",
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
        row["customer_site_metadata"] = assignment.get("metadata") or {}
        row["notes"] = assignment.get("notes") or ""
        row["report_metadata"] = assignment.get("report_metadata") or {}
        row["rating_metadata"] = assignment.get("rating_metadata") or {}
        row["is_report_ready"] = bool(assignment.get("is_report_ready"))
    return row


async def list_customer_sites(db: SupabaseAdmin, customer_id: str | None) -> list[dict[str, Any]]:
    if not customer_id:
        return []
    assignment_rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,site_id,account_id,metadata,notes,report_metadata,rating_metadata,is_report_ready,created_at",
            "customer_id": f"eq.{customer_id}",
            "order": "created_at.desc",
        },
    )
    site_ids = []
    for row in assignment_rows or []:
        site_id = row.get("site_id")
        if site_id and site_id not in site_ids:
            site_ids.append(site_id)
    if not site_ids:
        return []
    data = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,account_id,full_address,company_name,created_at,metadata",
            "site_id": f"in.({','.join(site_ids)})",
            "is_archived": "eq.false",
            "order": "created_at.desc",
        },
    )
    assignment_by_site = {row["site_id"]: row for row in assignment_rows or [] if row.get("site_id")}
    sites = []
    for row in data or []:
        assignment = assignment_by_site.get(row.get("site_id"))
        if not assignment:
            continue
        sites.append(
            {
                **row,
                "customer_site_id": assignment.get("customer_site_id"),
                "customer_site_metadata": assignment.get("metadata") or {},
                "notes": assignment.get("notes") or "",
                "report_metadata": assignment.get("report_metadata") or {},
                "rating_metadata": assignment.get("rating_metadata") or {},
                "is_report_ready": bool(assignment.get("is_report_ready")),
            }
        )
    return sites


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
    existing = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "customer_site_id,metadata",
            "customer_id": f"eq.{customer_id}",
            "site_id": f"eq.{site_id}",
            "limit": 1,
        },
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
        return {"customerSiteId": row["customer_site_id"], "metadata": metadata}
    created = await db.request(
        "POST",
        "/rest/v1/automatisor_customer_sites",
        params={"select": "customer_site_id,metadata"},
        json_body=payload,
        headers={"Prefer": "return=representation"},
    )
    result = created[0]
    return {"customerSiteId": result["customer_site_id"], "metadata": result.get("metadata") or metadata}


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
        assignment = await ensure_customer_site_assignment(
            db,
            customer_id,
            account_id,
            duplicate_row["site_id"],
            address_validation=customer_site_validation,
        )
        return {
            "status": "already_exists",
            "siteId": duplicate_row["site_id"],
            "customerSiteId": assignment["customerSiteId"],
        }
    created = await db.request(
        "POST",
        "/rest/v1/account_sites",
        params={"select": "site_id"},
        json_body=site_insert_row(account_id, company_name, site),
        headers={"Prefer": "return=representation"},
    )
    site_id = created[0].get("site_id") if created else None
    assignment = await ensure_customer_site_assignment(
        db,
        customer_id,
        account_id,
        site_id,
        address_validation=customer_site_validation,
    )
    return {"status": "created", "siteId": site_id, "customerSiteId": assignment["customerSiteId"]}


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
    return {**workspace, "sites": sites, "pre_assessment_price_credits": PRE_ASSESSMENT_PRICE}


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
              <a href="mailto:no-reply@automatisor.com" style="color:#7a7a72">no-reply@automatisor.com</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_pre_assessment_approval_email(email: str, company_name: str, site_address: str) -> None:
    if not RESEND_API_KEY:
        raise HTTPException(status_code=500, detail="Missing RESEND_API_KEY")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [email],
                "subject": "Your AutomatiSOR site pre-assessment report request is confirmed!",
                "html": build_pre_assessment_approval_email(email, company_name, site_address),
            },
        )
    if response.is_error:
        raise HTTPException(status_code=500, detail=f"Failed to send approval email: {response.text}")


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
        auth = get_auth_client()
        auth.auth.sign_in_with_otp(
            {
                "email": email,
                "options": {
                    "should_create_user": True,
                },
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def verify_supabase_otp(email: str, otp: str) -> dict[str, Any]:
    try:
        auth = get_auth_client()
        response = auth.auth.verify_otp(
            {
                "email": email,
                "token": otp,
                "type": "email",
            }
        )
        session = response.session
        user = response.user
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
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc) or "Invalid or expired OTP") from exc


@app.get("/api/frontend-config")
async def frontend_config() -> dict[str, Any]:
    return {"google_maps_api_key": GOOGLE_MAPS_API_KEY}


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


@app.get("/api/debug/google-status")
async def debug_google_status() -> dict[str, Any]:
    key = GOOGLE_MAPS_API_KEY or ""
    return {
        "google_maps_api_key_present": bool(key),
        "google_maps_api_key_length": len(key),
        "google_maps_api_key_prefix": key[:8] if key else "",
        "google_maps_api_key_suffix": key[-4:] if key else "",
    }


@app.post("/api/auth/check-email")
async def handle_check_email(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        return {"email": email, "user_mode": "existing_user" if customer else "new_user", "company_email": True}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/auth/request-otp")
async def handle_request_otp(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        await send_supabase_otp(email)
        return {
            "status": "otp_sent",
            "email": email,
            "user_mode": "existing_user" if customer else "new_user",
            "dry_run": is_dry_run_request(request, body),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/auth/verify-otp")
async def handle_verify_otp(response: Response, request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        otp = str(body.get("otp") or "").strip()
        if not re.fullmatch(r"\d{6}", otp):
            raise ValueError("Enter a valid 6-digit OTP")
        auth_data = await verify_supabase_otp(email, otp)
        session = auth_data.get("session") or {}
        if not session.get("access_token"):
            raise HTTPException(status_code=422, detail="Invalid or expired OTP")
        response.set_cookie(
            "access_token",
            session["access_token"],
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            path="/",
            max_age=60 * 60,
        )
        db = get_admin_db()
        customer = await find_customer_by_email(db, email)
        if not customer:
            return {
                "status": "verified",
                "email": email,
                "user_mode": "new_user",
                "next_step": "onboarding",
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/onboarding/complete")
async def handle_complete_onboarding(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        first_name = clean_required(body.get("first_name"), "First name")
        last_name = clean_required(body.get("last_name"), "Last name")
        designation = clean_required(body.get("designation"), "Designation")
        customer_company_name = clean_required(
            body.get("customer_company_name") or body.get("company_name"),
            "Company name",
        )
        customer_company_domain = normalize_domain(
            body.get("customer_company_domain") or body.get("company_domain")
        )
        dry_run = is_dry_run_request(request, body)

        account = {"accountId": None, "companyName": customer_company_name, "domain": customer_company_domain}
        customer = {"customerId": None}
        site_result = {"status": "pending_confirmation", "siteId": None}

        if not dry_run:
            db = get_admin_db()
            account = await upsert_account(db, customer_company_name, customer_company_domain)
            customer = await upsert_customer(
                db,
                {
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "designation": designation,
                    "companyName": customer_company_name,
                    "companyDomain": customer_company_domain,
                    "isVerified": True,
                    "touchLogin": True,
                },
            )
            await mark_customer_verified(db, email)
            workspace = await build_workspace_payload(db, email, account["accountId"])
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
            "customer_id": customer["customerId"],
            "account_id": account["accountId"],
            "company_name": customer_company_name,
            "company_domain": customer_company_domain,
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
            "dry_run": dry_run,
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/workspace/state")
async def workspace_state(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        db = get_admin_db()
        requested_account_id = clean_optional(body.get("active_account_id"))
        return await build_workspace_payload(db, email, requested_account_id)
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


@app.post("/api/auth/logout")
async def auth_logout(response: Response) -> dict[str, str]:
    response.delete_cookie("access_token", httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    return {"status": "logged_out"}


@app.post("/api/pre-assessment/request")
async def request_pre_assessment(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    try:
        email = assert_work_email(body.get("email") or body.get("work_email"))
        requested_account_id = clean_optional(body.get("account_id"))
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
        site = await find_account_site_by_id(
            db,
            requested_account_id or active_account["account_id"] if active_account else requested_account_id,
            site_id,
            customer["customer_id"],
        )
        if not site:
            raise HTTPException(status_code=422, detail="Selected site was not found for this customer")
        requested_at = datetime.now(timezone.utc).isoformat()
        if not is_dry_run_request(request, body):
            await ensure_customer_site_assignment(
                db,
                customer["customer_id"],
                site["account_id"],
                site["site_id"],
                requested_at=requested_at,
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
                    part
                    for part in [
                        customer.get("first_name") or "",
                        customer.get("last_name") or "",
                    ]
                    if part
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
        usage = await get_customer_usage_state(db, customer["customer_id"])
        return {
            "status": "running",
            "email": email,
            "account_id": site["account_id"],
            "site_id": site["site_id"],
            "customer_site_id": site.get("customer_site_id"),
            "credits_used_total": usage["creditsUsedTotal"],
            "credits_used_this_month": usage["creditsUsedThisMonth"],
            "pre_assessment_price_credits": PRE_ASSESSMENT_PRICE,
            "message": "Pre-assessment request approved. The job is running and the user will receive an email on completion.",
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
            "select": "site_id,usage_type,credits_used,created_at",
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

    signup_date: datetime | None = None
    raw_anchor = customer.get("email_verified_at")
    if raw_anchor:
        try:
            signup_date = datetime.fromisoformat(str(raw_anchor).replace("Z", "+00:00"))
        except ValueError:
            signup_date = None
    # Use earliest billing row if it predates the anchor (or if no anchor)
    if billing_rows:
        oldest_raw = min((r.get("created_at") or "" for r in billing_rows), default="")
        try:
            oldest_billing = datetime.fromisoformat(str(oldest_raw).replace("Z", "+00:00"))
            if signup_date is None or oldest_billing < signup_date:
                signup_date = oldest_billing
        except ValueError:
            pass
    if not signup_date:
        signup_date = datetime.now(timezone.utc)

    now = datetime.now(timezone.utc)

    def month_key(ts: datetime) -> str:
        # Convert to EST for month grouping
        est_offset = timedelta(hours=-5)
        local_ts = ts + est_offset
        return f"{local_ts.year}-{local_ts.month:02d}"

    def month_bounds(key: str) -> tuple[datetime, datetime]:
        import calendar as cal_mod
        year, month = int(key.split("-")[0]), int(key.split("-")[1])
        last_day = cal_mod.monthrange(year, month)[1]
        est_offset = timedelta(hours=-5)
        start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc) - est_offset
        end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc) - est_offset
        return start, end

    current_key = month_key(now)
    periods: dict[str, list[dict[str, Any]]] = {}
    for row in billing_rows:
        raw_ts = row.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        except ValueError:
            continue
        key = month_key(ts)
        if key not in periods:
            periods[key] = []
        site_id = row.get("site_id") or ""
        site_info = site_map.get(site_id, {})
        usage_type = row.get("usage_type") or ""
        task_label = USAGE_TYPE_LABELS.get(usage_type, usage_type.replace("_", " ").title())
        periods[key].append({
            "site_name": site_info.get("company_name") or "—",
            "site_address": site_info.get("full_address") or "—",
            "task_label": task_label,
            "timestamp_utc": raw_ts,
            "credits_used": int(row.get("credits_used") or 0),
        })

    if current_key not in periods:
        periods[current_key] = []

    result_periods = []
    for key in sorted(periods.keys(), reverse=True):
        start, end = month_bounds(key)
        rows = periods[key]
        result_periods.append({
            "period_index": key,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "is_current": key == current_key,
            "total_credits_used": sum(r["credits_used"] for r in rows),
            "rows": rows,
        })

    return {
        "billing_anchor_date": signup_date.isoformat(),
        "billing_periods": result_periods,
    }


@app.post("/api/billing/invoices")
async def get_billing_invoices(body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Email is required.")
    db = get_admin_db()
    customer = await find_customer_by_email(db, email)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    try:
        invoice_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_invoices",
            params={
                "select": "invoice_id,invoice_number,invoice_date,amount_usd,status,pdf_url,payment_url,period_start,period_end",
                "customer_id": f"eq.{customer['customer_id']}",
                "order": "invoice_date.desc",
            },
        )
    except HTTPException:
        invoice_rows = []
    return {"invoices": invoice_rows or []}


@app.post("/api/signup/request-otp")
async def signup_request_alias(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await handle_request_otp(request, body)


@app.post("/api/signup/verify-otp")
async def signup_verify_alias(response: Response, request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await handle_verify_otp(response, request, body)


@app.post("/api/accounts/new-user")
async def accounts_new_user_alias(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await handle_request_otp(request, body)


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
