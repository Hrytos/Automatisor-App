import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

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

router = APIRouter()

_REFUSAL_LINE = (
    "I can only answer questions about this report. "
    "I cannot write emails, campaigns, scripts, or outreach content."
)
_DISALLOWED_ACTION_PATTERN = re.compile(
    r"\b(write|draft|compose|frame|create|generate|prepare|craft|build|outline|make)\b",
    re.IGNORECASE,
)
_DISALLOWED_ARTIFACT_PATTERN = re.compile(
    r"\b(email|e-mail|message|campaign|sequence|follow-up|follow up|outreach|proposal|pitch deck|"
    r"sales pitch|cold call|call script|script|talk track|talking points|meeting script|cover letter)\b",
    re.IGNORECASE,
)
_DISALLOWED_OUTPUT_PATTERN = re.compile(
    r"(^subject\s*:|^hi\s|^hello\s|^dear\s|best regards|kind regards|sincerely,|thanks,\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


def _get_openai_key() -> str:
    """Read at request time so the value is always current after dotenv load."""
    return os.getenv("OPENAI_API_KEY", "")


def _serialize_context_payload(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value or "")


def _build_report_context_payload(site_row: dict) -> str:
    high_context = site_row.get("report_context_high")
    all_context = site_row.get("report_context_all")
    payload = {
        "report_context_high": high_context if high_context is not None else {},
        "report_context_all": all_context if all_context is not None else {},
    }
    return _serialize_context_payload(payload)


def _normalize_reply_text(raw_reply: str) -> str:
    reply = str(raw_reply or "").strip()
    if reply in {"", ".", "..", "...", "…"}:
        return "I can only answer questions about this report, and that information isn't available here."
    return reply


def _normalize_safety_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _is_disallowed_request(message: str) -> bool:
    normalized = _normalize_safety_text(message)
    has_action = bool(_DISALLOWED_ACTION_PATTERN.search(normalized))
    has_artifact = bool(_DISALLOWED_ARTIFACT_PATTERN.search(normalized))
    return has_action and has_artifact


def _build_refusal_follow_up(message: str) -> str:
    normalized = _normalize_safety_text(message)
    if "robot" in normalized or "robotic" in normalized or "picking" in normalized:
        return "Try asking: What does the report say about the fit, expected impact, and implementation considerations of robotic picking for this site?"
    if "staff" in normalized or "labor" in normalized or "workforce" in normalized:
        return "Try asking: What does the report say about staffing pressure, labor bottlenecks, and where automation could reduce manual workload at this site?"
    if "cost" in normalized or "roi" in normalized or "save" in normalized:
        return "Try asking: What does the report say about the likely operational benefits and cost-related drivers for automation at this site?"
    if "safety" in normalized or "injur" in normalized or "strain" in normalized:
        return "Try asking: What does the report say about safety risks, repetitive handling, and where automation could reduce physical strain at this site?"
    return "Try asking: What does the report say about the most relevant operational bottlenecks, automation opportunities, and expected impact for this site?"


def _build_disallowed_request_response(message: str) -> str:
    return f"{_REFUSAL_LINE}\n{_build_refusal_follow_up(message)}"


def _looks_like_disallowed_output(reply: str) -> bool:
    normalized = _normalize_reply_text(reply)
    return bool(_DISALLOWED_OUTPUT_PATTERN.search(normalized))



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

    return {"messages": rows[0].get("messages", [])}


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

    stored_messages: list = list(session_rows[0].get("messages", []) or [])
    is_first_message = len(stored_messages) == 0

    # Append user message
    now_iso = datetime.now(timezone.utc).isoformat()
    user_entry = {"role": "user", "content": message, "ts": now_iso}
    stored_messages.append(user_entry)

    # Build LLM window — last 20 messages
    llm_window = stored_messages[-20:]
    llm_messages = [
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(
            report_context=report_context
        )},
        *[{"role": m["role"], "content": m["content"]} for m in llm_window],
    ]

    if _is_disallowed_request(message):
        reply_text = _build_disallowed_request_response(message)
    else:
        # Call OpenAI
        try:
            client = AsyncOpenAI(api_key=_get_openai_key())
            completion = await client.chat.completions.create(
                model="gpt-5-mini",
                messages=llm_messages,
            )
            reply_text = _normalize_reply_text(completion.choices[0].message.content or "")
            if _looks_like_disallowed_output(reply_text):
                reply_text = _build_disallowed_request_response(message)
        except Exception as exc:
            return JSONResponse(status_code=502, content={"detail": f"LLM error: {exc}"})

    # Append assistant reply
    assistant_entry = {"role": "assistant", "content": reply_text, "ts": datetime.now(timezone.utc).isoformat()}
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

    return {"reply": reply_text, "title": auto_title}
