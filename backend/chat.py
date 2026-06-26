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
    from .chat_prompt import SYSTEM_PROMPT_TEMPLATE
except ImportError:
    from chat_prompt import SYSTEM_PROMPT_TEMPLATE

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



# ---------------------------------------------------------------------------
# GET /api/chat/sessions?site_id=...
# ---------------------------------------------------------------------------
@router.get("/api/chat/sessions")
async def list_sessions(request: Request, site_id: str = ""):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
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

    # Verify ownership
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

    # Fetch sessions
    try:
        rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "is_archived": "eq.false",
                "select": "session_id,title,created_at,updated_at",
                "order": "updated_at.desc",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return {"sessions": rows or []}


# ---------------------------------------------------------------------------
# GET /api/chat/history?site_id=...&session_id=...
# ---------------------------------------------------------------------------
@router.get("/api/chat/history")
async def get_history(request: Request, site_id: str = "", session_id: str = ""):
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    if not site_id or not session_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "site_id and session_id are required"},
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

    # Verify session ownership
    try:
        rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
                "select": "messages",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not rows:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})

    return {"messages": _ensure_message_list_schema(list(rows[0].get("messages", []) or []))}


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

    # Verify session ownership and fetch messages
    try:
        session_rows = await db.request(
            "GET",
            "/rest/v1/automatisor_chatbot",
            params={
                "session_id": f"eq.{session_id}",
                "customer_id": f"eq.{customer_id}",
                "site_id": f"eq.{site_id}",
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
            },
            json_body=patch_body,
        )
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"detail": "Failed to save chat history. Please retry."},
        )

    return {"reply": reply_text, "title": auto_title, "assistant_message_id": assistant_message_id}


_SAMPLE_REPORT_DIR = _BACKEND_DIR / "sample-report"
_sample_report_context_cache: dict[str, str] | None = None


def _load_sample_report_context() -> dict[str, str]:
    """Load BR Williams sample report context (cached after first read)."""
    global _sample_report_context_cache
    if _sample_report_context_cache is not None:
        return _sample_report_context_cache

    high_path = _SAMPLE_REPORT_DIR / "br_williams_high.json"
    all_path = _SAMPLE_REPORT_DIR / "br_williams_all.json"
    with open(high_path, encoding="utf-8") as high_file:
        high_context = json.load(high_file)
    with open(all_path, encoding="utf-8") as all_file:
        all_context = json.load(all_file)

    _sample_report_context_cache = {
        "report_context_high": _serialize_context_payload(high_context),
        "report_context_all": _serialize_context_payload(all_context),
    }
    return _sample_report_context_cache


@router.post("/api/chat/sample/message")
async def send_sample_message(request: Request):
    """Stateless chat for the BR Williams sample report — no DB, ephemeral per page visit."""
    SupabaseAdmin, get_authenticated_customer, infer_error_status = _resolve_main_deps()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON body"})

    message = str(body.get("message") or "").strip()
    prior_messages = body.get("messages") or []

    if not message:
        return JSONResponse(status_code=400, content={"detail": "message cannot be empty"})
    if len(message) > 2000:
        return JSONResponse(
            status_code=400,
            content={"detail": "message must be 2000 characters or fewer"},
        )
    if not isinstance(prior_messages, list):
        return JSONResponse(status_code=400, content={"detail": "messages must be a list"})

    OPENAI_API_KEY = _get_openai_key()
    if not OPENAI_API_KEY:
        return JSONResponse(
            status_code=500,
            content={"detail": "OpenAI API key is not configured"},
        )

    db = SupabaseAdmin()
    try:
        await get_authenticated_customer(db, request)
    except HTTPException:
        raise
    except Exception as exc:
        detail = str(exc)
        return JSONResponse(status_code=infer_error_status(detail), content={"detail": detail})

    report_context = _load_sample_report_context()
    llm_window: list[dict[str, str]] = []
    for entry in prior_messages[-19:]:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip()
        content = str(entry.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            llm_window.append({"role": role, "content": content})
    llm_window.append({"role": "user", "content": message})

    llm_messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT_TEMPLATE.format(
                report_context_high=report_context["report_context_high"],
                report_context_all=report_context["report_context_all"],
                user_context="",
            ),
        },
        *llm_window,
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

    return {"reply": reply_text}


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
                "select": "session_id,title,messages",
                "limit": "1",
            },
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if not session_rows:
        return JSONResponse(status_code=404, content={"detail": "Chat session not found"})

    source_session = session_rows[0]
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
