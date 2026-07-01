import json
import os
import asyncio
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI

try:
    from .chat_prompt import FACILITIES_SYSTEM_PROMPT_TEMPLATE, SYSTEM_PROMPT_TEMPLATE
except ImportError:
    from chat_prompt import FACILITIES_SYSTEM_PROMPT_TEMPLATE, SYSTEM_PROMPT_TEMPLATE

# Ensure .env is loaded even when this module is imported before main.py runs load_dotenv
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR.parent / ".env")
load_dotenv(_BACKEND_DIR / ".env")


def _resolve_main_deps():
    try:
        from .main import SupabaseAdmin, get_authenticated_customer, infer_error_status
    except ImportError:
        from main import SupabaseAdmin, get_authenticated_customer, infer_error_status
    return SupabaseAdmin, get_authenticated_customer, infer_error_status


def _resolve_share_deps():
    try:
        from .main import (
            SupabaseAdmin,
            create_chat_mirror_for_share,
            encode_share_token,
            ensure_chat_shared_site_assignment,
            find_customer_by_email,
            get_app_base_url,
            get_authenticated_customer,
            infer_error_status,
            send_chat_share_email,
            validate_share_recipients,
        )
    except ImportError:
        from main import (
            SupabaseAdmin,
            create_chat_mirror_for_share,
            encode_share_token,
            ensure_chat_shared_site_assignment,
            find_customer_by_email,
            get_app_base_url,
            get_authenticated_customer,
            infer_error_status,
            send_chat_share_email,
            validate_share_recipients,
        )
    return (
        SupabaseAdmin,
        create_chat_mirror_for_share,
        encode_share_token,
        ensure_chat_shared_site_assignment,
        find_customer_by_email,
        get_app_base_url,
        get_authenticated_customer,
        infer_error_status,
        send_chat_share_email,
        validate_share_recipients,
    )

router = APIRouter()

CHAT_TYPE_SITE = "site"
CHAT_TYPE_FACILITY = "facility"

SITE_CHAT_SOURCE_TYPES = (
    "OSHA Establishment",
    "Building Permit",
    "Image Analysis",
    "OSHA Accidents",
)


def _get_openai_key() -> str:
    """Read at request time so the value is always current after dotenv load."""
    return os.getenv("OPENAI_API_KEY", "")


def _serialize_context_payload(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value or "")


def _build_report_context_payload(site_row: dict) -> dict:
    high_context = site_row.get("report_context_high")
    all_context = site_row.get("report_context_all")
    high_context_str = _serialize_context_payload(high_context if high_context is not None else {})
    all_context_str = _serialize_context_payload(all_context if all_context is not None else {})
    return {
        "report_context_high": high_context_str,
        "report_context_all": all_context_str,
    }


def _build_user_context_payload(row: dict | None) -> str:
    metadata = row.get("metadata") if isinstance(row, dict) else {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("context") or "").strip()


def _metadata_has_content(metadata: Any) -> bool:
    if metadata is None:
        return False
    if isinstance(metadata, dict):
        if not metadata:
            return False
        return any(_metadata_has_content(value) for value in metadata.values())
    if isinstance(metadata, list):
        if not metadata:
            return False
        return any(_metadata_has_content(value) for value in metadata)
    if isinstance(metadata, str):
        return bool(metadata.strip())
    return True


def _build_source_site_context_from_rows(rows: list[dict] | None) -> dict[str, str]:
    allowed = set(SITE_CHAT_SOURCE_TYPES)
    payload: dict[str, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        source_type = row.get("source_type")
        if source_type not in allowed or source_type in payload:
            continue
        metadata = row.get("metadata")
        if not _metadata_has_content(metadata):
            continue
        payload[str(source_type)] = _serialize_context_payload(metadata)
    return payload


def _format_source_site_context_prompt_section(source_context: dict[str, str]) -> str:
    if not source_context:
        return ""
    sections: list[str] = []
    for source_type in SITE_CHAT_SOURCE_TYPES:
        content = source_context.get(source_type)
        if not content:
            continue
        sections.append(f"### {source_type}\n\n{content}")
    if not sections:
        return ""
    body = "\n\n---\n\n".join(sections)
    return (
        "---\n\n"
        "## SUPPORTING SOURCE EVIDENCE (internal reference — do not recite as a separate section)\n\n"
        "Use only to corroborate or enrich report-based answers, or to answer when the report is silent — "
        "woven naturally into your response per SUPPORTING SOURCE EVIDENCE RULES above.\n"
        "When the report's **high-confidence** context conflicts with this evidence, prefer the report.\n\n"
        f"{body}\n\n"
        "---"
    )


async def _fetch_site_source_context(db, site_id: str) -> dict[str, str]:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_source_sites",
        params={
            "site_id": f"eq.{site_id}",
            "select": "source_type,metadata",
        },
    )
    return _build_source_site_context_from_rows(rows)


def _normalize_reply_text(raw_reply: str) -> str:
    return str(raw_reply or "").strip()


def _ensure_message_schema(message: dict[str, object]) -> dict[str, object]:
    normalized = dict(message or {})
    if not normalized.get("id"):
        normalized["id"] = uuid4().hex
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        normalized["metadata"] = {}
    return normalized


def _ensure_message_list_schema(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [_ensure_message_schema(message) for message in messages]


def _store_feedback_on_messages(messages: list[dict[str, object]], message_id: str, feedback: str) -> list[dict[str, object]]:
    next_messages: list[dict[str, object]] = []
    for message in _ensure_message_list_schema(messages):
        current = dict(message)
        if current.get("id") == message_id and current.get("role") == "assistant":
            metadata = dict(current.get("metadata") or {})
            metadata["feedback"] = {
                "value": feedback,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            current["metadata"] = metadata
        next_messages.append(current)
    return next_messages


def _resolve_facilities_deps():
    try:
        from .main import list_customer_sites
    except ImportError:
        from main import list_customer_sites
    return list_customer_sites


async def _fetch_ready_assignments_with_context(db, customer_id: str) -> list[dict]:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_customer_sites",
        params={
            "select": "site_id,assigned_via,is_report_ready,report_context_high,report_context_all",
            "customer_id": f"eq.{customer_id}",
            "assigned_via": "neq.sample_site",
            "is_report_ready": "eq.true",
        },
    )
    return rows or []


def _has_report_context(row: dict | None) -> bool:
    if not isinstance(row, dict):
        return False
    for key in ("report_context_high", "report_context_all"):
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, str) and value.strip() not in ("", "{}", "[]"):
            return True
    return False


def _collect_ready_facility_reports(
    sites_by_id: dict[str, dict],
    assignments: list[dict],
) -> list[dict]:
    """Return report-backed entries for facilities with is_report_ready=true and context."""
    reports: list[dict] = []
    seen_site_ids: set[str] = set()

    for assignment in assignments:
        if not _has_report_context(assignment):
            continue
        site_id = str(assignment.get("site_id") or "").strip()
        if not site_id or site_id in seen_site_ids:
            continue
        seen_site_ids.add(site_id)
        site = sites_by_id.get(site_id, {})
        report_context = _build_report_context_payload(assignment)
        tag = "Shared with me" if assignment.get("assigned_via") == "shared_site" else "Added by me"
        reports.append(
            {
                "site_id": site_id,
                "company_name": str(site.get("company_name") or "").strip(),
                "full_address": str(site.get("full_address") or "").strip(),
                "tag": tag,
                "report_context_high": report_context["report_context_high"],
                "report_context_all": report_context["report_context_all"],
            }
        )

    return reports


def _build_facilities_context_payload(reports: list[dict]) -> dict[str, str]:
    return {
        "facility_reports": json.dumps(reports, ensure_ascii=False, indent=2),
    }


async def _load_facilities_chat_context(db, customer_id: str) -> dict[str, str]:
    list_customer_sites = _resolve_facilities_deps()
    sites = await list_customer_sites(db, customer_id)
    sites_by_id = {
        str(site.get("site_id")): site for site in sites if site.get("site_id")
    }
    assignments = await _fetch_ready_assignments_with_context(db, customer_id)
    reports = _collect_ready_facility_reports(sites_by_id, assignments)
    return _build_facilities_context_payload(reports)


async def _get_facilities_session_messages(db, customer_id: str, session_id: str) -> list[dict] | None:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_chatbot",
        params={
            "session_id": f"eq.{session_id}",
            "customer_id": f"eq.{customer_id}",
            "chat_type": f"eq.{CHAT_TYPE_FACILITY}",
            "site_id": "is.null",
            "select": "messages",
            "limit": "1",
        },
    )
    if not rows:
        return None
    return _ensure_message_list_schema(list(rows[0].get("messages", []) or []))


def _session_title_base(title: str | None, created_at: str | None) -> str:
    if title and str(title).strip():
        return str(title).strip()
    if created_at:
        try:
            normalized = str(created_at).replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            return dt.strftime("Chat - %m/%d/%Y %I:%M %p")
        except ValueError:
            pass
    return "Chat"


def _street_city_from_full_address(full_address: str) -> str:
    parts = [part.strip() for part in str(full_address or "").split(",") if part.strip()]
    if len(parts) >= 2:
        return f"{parts[0]}, {parts[1]}"
    return parts[0] if parts else ""


def _format_session_display_label(row: dict, site_meta: dict | None) -> str:
    title_base = _session_title_base(row.get("title"), row.get("created_at"))
    chat_type = str(row.get("chat_type") or CHAT_TYPE_SITE).strip()
    if chat_type == CHAT_TYPE_FACILITY:
        return f"{title_base} - facility"
    company = str((site_meta or {}).get("company_name") or "").strip()
    address = _street_city_from_full_address(str((site_meta or {}).get("full_address") or ""))
    if company and address:
        location = f"{company}({address})"
    else:
        location = company or address or "site"
    return f"{title_base} - {location}"


async def _fetch_site_display_metadata(db, site_ids: list[str]) -> dict[str, dict]:
    unique_ids = [site_id for site_id in dict.fromkeys(site_ids) if site_id]
    if not unique_ids:
        return {}
    rows = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,company_name,full_address",
            "site_id": f"in.({','.join(unique_ids)})",
        },
    )
    return {str(row.get("site_id")): row for row in (rows or []) if row.get("site_id")}


async def _enrich_session_rows(db, rows: list[dict]) -> list[dict]:
    site_ids = [str(row.get("site_id")) for row in rows if row.get("site_id")]
    site_meta_by_id = await _fetch_site_display_metadata(db, site_ids)
    enriched: list[dict] = []
    for row in rows:
        site_id = str(row.get("site_id") or "").strip() or None
        site_meta = site_meta_by_id.get(site_id or "", None)
        enriched.append(
            {
                **row,
                "chat_type": row.get("chat_type") or CHAT_TYPE_SITE,
                "site_id": site_id,
                "display_label": _format_session_display_label(row, site_meta),
            }
        )
    return enriched


async def _get_customer_session_row(
    db,
    customer_id: str,
    session_id: str,
    *,
    select: str = "session_id,title,created_at,updated_at,chat_type,site_id,messages",
) -> dict | None:
    rows = await db.request(
        "GET",
        "/rest/v1/automatisor_chatbot",
        params={
            "session_id": f"eq.{session_id}",
            "customer_id": f"eq.{customer_id}",
            "is_archived": "eq.false",
            "select": select,
            "limit": "1",
        },
    )
    if not rows:
        return None
    return rows[0]


async def _save_facilities_session_messages(
    db,
    customer_id: str,
    session_id: str,
    messages: list[dict],
    *,
    title: str | None = None,
) -> None:
    patch_body: dict = {
        "messages": messages,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if title:
        patch_body["title"] = title
    await db.request(
        "PATCH",
        "/rest/v1/automatisor_chatbot",
        params={
            "session_id": f"eq.{session_id}",
            "customer_id": f"eq.{customer_id}",
            "chat_type": f"eq.{CHAT_TYPE_FACILITY}",
            "site_id": "is.null",
        },
        json_body=patch_body,
    )


# ---------------------------------------------------------------------------
# GET /api/chat/sessions — unified history for the authenticated customer
# ---------------------------------------------------------------------------
@router.get("/api/chat/sessions")
async def list_sessions(request: Request, site_id: str = ""):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]

    try:
        rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "customer_id": f"eq.{customer_id}",
                "is_archived": "eq.false",
                "select": "session_id,title,created_at,updated_at,chat_type,site_id",
                "order": "updated_at.desc",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    sessions = await _enrich_session_rows(db, rows or [])
    if site_id:
        sessions = [row for row in sessions if row.get("chat_type") == CHAT_TYPE_SITE and row.get("site_id") == site_id]
    return {"sessions": sessions}


# ---------------------------------------------------------------------------
# GET /api/chat/history?session_id=...
# ---------------------------------------------------------------------------
@router.get("/api/chat/history")
async def get_history(request: Request, site_id: str = "", session_id: str = ""):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    if not session_id:
        return JSONResponse(status_code=400, content={"detail": "session_id is required"})

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]

    try:
        row = await _get_customer_session_row(db, customer_id, session_id)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not row:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    if site_id and row.get("chat_type") == CHAT_TYPE_SITE and str(row.get("site_id") or "") != site_id:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    enriched = (await _enrich_session_rows(db, [row]))[0]
    return {
        "messages": _ensure_message_list_schema(list(row.get("messages", []) or [])),
        "session": {
            "session_id": enriched.get("session_id"),
            "title": enriched.get("title"),
            "chat_type": enriched.get("chat_type"),
            "site_id": enriched.get("site_id"),
            "display_label": enriched.get("display_label"),
            "created_at": enriched.get("created_at"),
            "updated_at": enriched.get("updated_at"),
        },
    }


# ---------------------------------------------------------------------------
# POST /api/chat/session — create new session
# ---------------------------------------------------------------------------
@router.post("/api/chat/session")
async def create_session(request: Request):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    site_id = body.get("site_id", "").strip()
    title = body.get("title", "").strip() or None

    if not site_id:
        return JSONResponse(status_code=400, content={"detail": "site_id is required"})

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]

    # Verify site ownership
    try:
        site_resp = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "select": "site_id",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not site_resp:
        return JSONResponse(status_code=404, content={"detail": "Site not found"})

    # Create session
    try:
        insert_resp = await db.request(
            "POST",
            "/rest/v1/automatisor_chatbot",
            json_body={
                "customer_id": customer_id,
                "site_id": site_id,
                "chat_type": CHAT_TYPE_SITE,
                "title": title,
                "messages": [],
            },
            headers={"Prefer": "return=representation"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not insert_resp:
        return JSONResponse(status_code=500, content={"detail": "Failed to create session"})

    row = insert_resp[0] if isinstance(insert_resp, list) else insert_resp
    return {"session_id": row["session_id"]}


# ---------------------------------------------------------------------------
# POST /api/chat/message — send a message and get an AI reply
# ---------------------------------------------------------------------------
@router.post("/api/chat/message")
async def send_message(request: Request):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    site_id = body.get("site_id", "").strip()
    session_id = body.get("session_id", "").strip()
    message = body.get("message", "").strip()

    if not site_id or not session_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "site_id and session_id are required"},
        )
    if not message:
        return JSONResponse(status_code=400, content={"detail": "message cannot be empty"})
    if len(message) > 2000:
        return JSONResponse(
            status_code=400,
            content={"detail": "message must be 2000 characters or fewer"},
        )

    OPENAI_API_KEY = _get_openai_key()
    if not OPENAI_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"detail": "OpenAI API key is not configured"},
        )

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]

    # Fetch site — verify ownership and get both report context columns
    try:
        site_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_sites",
            params={
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "select": "report_context_high,report_context_all",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not site_rows:
        return JSONResponse(status_code=404, content={"detail": "Site not found"})

    report_context = _build_report_context_payload(site_rows[0])
    try:
        user_context_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_context",
            params={
                "customer_id": f"eq.{customer_id}",
                "event_type": "eq.user_context",
                "select": "metadata,updated_at",
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})
    user_context = _build_user_context_payload(user_context_rows[0] if user_context_rows else None)

    try:
        source_site_context = await _fetch_site_source_context(db, site_id)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})
    source_site_context_block = _format_source_site_context_prompt_section(source_site_context)

    # Verify session ownership and fetch messages
    try:
        session_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "chat_type": f"eq.{CHAT_TYPE_SITE}",
                "select": "messages",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not session_rows:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    stored_messages: list = _ensure_message_list_schema(list(session_rows[0].get("messages", []) or []))
    is_first_message = len(stored_messages) == 0

    # Append user message
    now_iso = datetime.now(timezone.utc).isoformat()
    user_entry = {"id": uuid4().hex, "role": "user", "content": message, "ts": now_iso}
    stored_messages.append(user_entry)

    # Build LLM window — last 20 messages
    llm_window = stored_messages[-20:]
    llm_messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT_TEMPLATE.format(
                report_context_high=report_context["report_context_high"],
                report_context_all=report_context["report_context_all"],
                source_site_context=source_site_context_block,
                user_context=user_context,
            ),
        },
        *[{"role": m["role"], "content": m["content"]} for m in llm_window],
    ]

    # Call OpenAI
    try:
        client = AsyncOpenAI(api_key=_get_openai_key())
        completion = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=llm_messages,
        )
        reply_text = _normalize_reply_text(completion.choices[0].message.content or "")
    except Exception as exc:
        return JSONResponse(status_code=502, content={"detail": f"LLM error: {exc}"})

    # Append assistant reply
    assistant_message_id = uuid4().hex
    assistant_entry = {
        "id": assistant_message_id,
        "role": "assistant",
        "content": reply_text,
        "ts": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }
    stored_messages.append(assistant_entry)

    # Persist updated messages (+ auto-title on first message)
    auto_title = (message[:60] + "…") if is_first_message and len(message) > 60 else (message if is_first_message else None)
    patch_body: dict = {
        "messages": stored_messages,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if auto_title:
        patch_body["title"] = auto_title
    try:
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "chat_type": f"eq.{CHAT_TYPE_SITE}",
            },
            json_body=patch_body,
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to save chat history. Please retry."},
        )

    return {"reply": reply_text, "title": auto_title, "assistant_message_id": assistant_message_id}


@router.post("/api/chat/feedback")
async def chat_feedback(request: Request):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    site_id = body.get("site_id", "").strip()
    session_id = body.get("session_id", "").strip()
    message_id = body.get("message_id", "").strip()
    feedback = body.get("feedback", "").strip().lower()

    if not site_id or not session_id or not message_id:
        return JSONResponse(status_code=400, content={"detail": "site_id, session_id, and message_id are required"})
    if feedback not in {"up", "down"}:
        return JSONResponse(status_code=400, content={"detail": "feedback must be 'up' or 'down'"})

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]

    try:
        session_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "chat_type": f"eq.{CHAT_TYPE_SITE}",
                "select": "messages",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not session_rows:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    messages = _ensure_message_list_schema(list(session_rows[0].get("messages", []) or []))
    updated_messages = _store_feedback_on_messages(messages, message_id, feedback)

    try:
        await db.request(
            "PATCH",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "chat_type": f"eq.{CHAT_TYPE_SITE}",
            },
            json_body={
                "messages": updated_messages,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return {"ok": True}


# ---------------------------------------------------------------------------
# Facilities workspace chat — multi-facility context scoped by UI filter
# ---------------------------------------------------------------------------
@router.get("/api/chat/facilities/sessions")
async def list_facilities_sessions(request: Request):
    response = await list_sessions(request)
    if isinstance(response, JSONResponse):
        return response
    sessions = [
        row for row in (response.get("sessions") or []) if row.get("chat_type") == CHAT_TYPE_FACILITY
    ]
    return {"sessions": sessions}


@router.get("/api/chat/facilities/history")
async def get_facilities_history(request: Request, session_id: str = ""):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    if not session_id:
        return JSONResponse(status_code=400, content={"detail": "session_id is required"})

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]
    messages = await _get_facilities_session_messages(db, customer_id, session_id)
    if messages is None:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    return {"messages": messages}


@router.post("/api/chat/facilities/session")
async def create_facilities_session(request: Request):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    title = str(body.get("title") or "").strip() or None
    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]

    try:
        insert_resp = await db.request(
            "POST",
            "/rest/v1/automatisor_chatbot",
            json_body={
                "customer_id": customer_id,
                "chat_type": CHAT_TYPE_FACILITY,
                "title": title,
                "messages": [],
            },
            headers={"Prefer": "return=representation"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not insert_resp:
        return JSONResponse(status_code=500, content={"detail": "Failed to create session"})

    row = insert_resp[0] if isinstance(insert_resp, list) else insert_resp
    return {"session_id": row["session_id"]}


@router.post("/api/chat/facilities/message")
async def send_facilities_message(request: Request):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    session_id = str(body.get("session_id") or "").strip()
    message = str(body.get("message") or "").strip()

    if not session_id:
        return JSONResponse(status_code=400, content={"detail": "session_id is required"})
    if not message:
        return JSONResponse(status_code=400, content={"detail": "message cannot be empty"})
    if len(message) > 2000:
        return JSONResponse(
            status_code=400,
            content={"detail": "message must be 2000 characters or fewer"},
        )

    if not _get_openai_key():
        return JSONResponse(status_code=500, content={"detail": "OpenAI API key is not configured"})

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]
    stored_messages = await _get_facilities_session_messages(db, customer_id, session_id)
    if stored_messages is None:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    facilities_context = await _load_facilities_chat_context(db, customer_id)

    try:
        user_context_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_customer_context",
            params={
                "customer_id": f"eq.{customer_id}",
                "event_type": "eq.user_context",
                "select": "metadata,updated_at",
                "order": "updated_at.desc",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})
    user_context = _build_user_context_payload(user_context_rows[0] if user_context_rows else None)

    is_first_message = len(stored_messages) == 0
    now_iso = datetime.now(timezone.utc).isoformat()
    user_entry = {"id": uuid4().hex, "role": "user", "content": message, "ts": now_iso}
    stored_messages.append(user_entry)

    llm_window = stored_messages[-20:]
    llm_messages = [
        {
            "role": "system",
            "content": FACILITIES_SYSTEM_PROMPT_TEMPLATE.format(
                facility_reports=facilities_context["facility_reports"],
                user_context=user_context,
            ),
        },
        *[{"role": m["role"], "content": m["content"]} for m in llm_window],
    ]

    try:
        client = AsyncOpenAI(api_key=_get_openai_key())
        completion = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=llm_messages,
        )
        reply_text = _normalize_reply_text(completion.choices[0].message.content or "")
    except Exception as exc:
        return JSONResponse(status_code=502, content={"detail": f"LLM error: {exc}"})

    assistant_message_id = uuid4().hex
    assistant_entry = {
        "id": assistant_message_id,
        "role": "assistant",
        "content": reply_text,
        "ts": datetime.now(timezone.utc).isoformat(),
        "metadata": {},
    }
    stored_messages.append(assistant_entry)

    auto_title = (message[:60] + "…") if is_first_message and len(message) > 60 else (message if is_first_message else None)
    try:
        await _save_facilities_session_messages(
            db,
            customer_id,
            session_id,
            stored_messages,
            title=auto_title,
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to save chat history. Please retry."},
        )

    return {"reply": reply_text, "title": auto_title, "assistant_message_id": assistant_message_id}


@router.post("/api/chat/facilities/feedback")
async def facilities_chat_feedback(request: Request):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    session_id = str(body.get("session_id") or "").strip()
    message_id = str(body.get("message_id") or "").strip()
    feedback = str(body.get("feedback") or "").strip().lower()

    if not session_id or not message_id:
        return JSONResponse(status_code=400, content={"detail": "session_id and message_id are required"})
    if feedback not in {"up", "down"}:
        return JSONResponse(status_code=400, content={"detail": "feedback must be 'up' or 'down'"})

    db = SupabaseAdmin()

    try:
        customer = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    customer_id = customer["customer_id"]
    messages = await _get_facilities_session_messages(db, customer_id, session_id)
    if messages is None:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    updated_messages = _store_feedback_on_messages(messages, message_id, feedback)
    try:
        await _save_facilities_session_messages(db, customer_id, session_id, updated_messages)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/chat/share — share a chat session snapshot with work-email recipients
# ---------------------------------------------------------------------------
@router.post("/api/chat/share")
async def share_chat(request: Request):
    (
        SupabaseAdmin,
        create_chat_mirror_for_share,
        encode_share_token,
        ensure_chat_shared_site_assignment,
        find_customer_by_email,
        get_app_base_url,
        get_authenticated_customer,
        infer_error_status,
        send_chat_share_email,
        validate_share_recipients,
    ) = _resolve_share_deps()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    site_id = str(body.get("site_id") or "").strip()
    session_id = str(body.get("session_id") or "").strip()
    if not site_id or not session_id:
        return JSONResponse(status_code=400, content={"detail": "site_id and session_id are required"})

    db = SupabaseAdmin()

    try:
        sender = await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    sender_id = sender["customer_id"]
    sender_email = sender.get("email") or ""

    try:
        session_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{sender_id}",
                "site_id": f"eq.{site_id}",
                "chat_type": f"eq.{CHAT_TYPE_SITE}",
                "select": "session_id,title,messages,chat_type",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not session_rows:
        return JSONResponse(status_code=404, content={"detail": "Chat session not found"})

    source_session = session_rows[0]
    if source_session.get("chat_type") == CHAT_TYPE_FACILITY:
        return JSONResponse(
            status_code=422,
            content={"detail": "Facilities workspace chats cannot be shared."},
        )
    messages = _ensure_message_list_schema(list(source_session.get("messages", []) or []))
    if not messages:
        return JSONResponse(status_code=422, content={"detail": "Cannot share an empty conversation"})

    recipients, recipient_errors = validate_share_recipients(body.get("emails"), sender_email)
    if recipient_errors:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "message": "Correct the rejected email addresses before sending.",
                    "recipient_errors": recipient_errors,
                }
            },
        )
    app_base_url = get_app_base_url(request)
    if not app_base_url:
        return JSONResponse(status_code=500, content={"detail": "Missing APP_BASE_URL"})

    site_rows = await db.request(
        "GET",
        "/rest/v1/account_sites",
        params={
            "select": "site_id,company_name,full_address",
            "site_id": f"eq.{site_id}",
            "limit": 1,
        },
    )
    site = site_rows[0] if site_rows else {}
    chat_title = str(source_session.get("title") or "Shared conversation")
    semaphore = asyncio.Semaphore(3)

    async def share_with_recipient(recipient: str) -> dict[str, str]:
        try:
            recipient_customer = await find_customer_by_email(db, recipient)
            if recipient_customer:
                await ensure_chat_shared_site_assignment(
                    db,
                    recipient_customer["customer_id"],
                    site_id,
                    sender_id,
                )
                await create_chat_mirror_for_share(
                    db,
                    site_id=site_id,
                    title=chat_title,
                    messages=messages,
                    source_session_id=session_id,
                    shared_by_customer_id=sender_id,
                    customer_id=recipient_customer["customer_id"],
                )
            else:
                await create_chat_mirror_for_share(
                    db,
                    site_id=site_id,
                    title=chat_title,
                    messages=messages,
                    source_session_id=session_id,
                    shared_by_customer_id=sender_id,
                    pending_recipient_email=recipient,
                )
        except Exception as exc:
            return {"email": recipient, "status": "failed", "message": f"Could not prepare share: {exc}"}

        token = encode_share_token(
            recipient,
            site_id,
            sender_id,
            session_id=session_id,
            share_type="chat",
        )
        share_url = f"{app_base_url}/auth?share={token}"
        try:
            async with semaphore:
                await send_chat_share_email(
                    recipient,
                    sender_email,
                    site.get("company_name") or "",
                    site.get("full_address") or "",
                    chat_title,
                    share_url,
                )
            return {"email": recipient, "status": "sent"}
        except Exception as exc:
            error_msg = str(exc)
            if "rate limit" in error_msg.lower() or "429" in error_msg:
                return {"email": recipient, "status": "failed", "message": "Rate limit reached, try again later"}
            if "getaddrinfo" in error_msg.lower() or "could not reach email service" in error_msg.lower():
                return {
                    "email": recipient,
                    "status": "failed",
                    "message": "Could not reach email service. Check your internet connection and try again.",
                }
            return {"email": recipient, "status": "failed", "message": error_msg}

    results = await asyncio.gather(*(share_with_recipient(recipient) for recipient in recipients))
    return {
        "status": "complete",
        "sent": sum(result["status"] == "sent" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
        "results": results,
    }
