import json
import os
import re
from enum import Enum
from uuid import uuid4
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
_ARTIFACT_REQUEST_PATTERN = re.compile(
    r"\b(csv|xlsx|excel|spreadsheet|pdf|doc|docx|ppt|pptx|powerpoint|slide deck|"
    r"download(?:able)?|download link|export|import|attachment|file)\b",
    re.IGNORECASE,
)
_ARTIFACT_OFFER_PATTERN = re.compile(
    r"\b(would you like|if you want|i can|can provide|shall i|should i|"
    r"download(?:able)?|download link|export|import|csv|xlsx|excel|spreadsheet|"
    r"pdf|doc|docx|ppt|pptx|powerpoint|slide deck|attachment|file)\b",
    re.IGNORECASE,
)
_MISSING_DATA_REPLY_PATTERN = re.compile(
    r"\b(not stated|not available|not included|does not provide|doesn't provide|"
    r"no explicit|no mention|cannot confirm|can't confirm|cannot determine|"
    r"don't have|do not have|lacks|lacking|unclear|unknown)\b",
    re.IGNORECASE,
)
class ChatIntent(str, Enum):
    REPORT_ANALYSIS = "report_analysis"
    OUT_OF_SCOPE_ARTIFACT_REQUEST = "out_of_scope_artifact_request"
    OUT_OF_SCOPE_EXTERNAL_CONTENT = "out_of_scope_external_content"


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
        return (
            "That information is not available in this report.\n"
            "If you want, I can check the closest related detail that is available in this site report."
        )
    return reply


def _normalize_safety_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _is_disallowed_request(message: str) -> bool:
    normalized = _normalize_safety_text(message)
    has_action = bool(_DISALLOWED_ACTION_PATTERN.search(normalized))
    has_artifact = bool(_DISALLOWED_ARTIFACT_PATTERN.search(normalized))
    return has_action and has_artifact


def _is_artifact_request(message: str) -> bool:
    normalized = _normalize_safety_text(message)
    return bool(_ARTIFACT_REQUEST_PATTERN.search(normalized))


def _classify_intent(message: str) -> ChatIntent:
    if _is_disallowed_request(message):
        return ChatIntent.OUT_OF_SCOPE_EXTERNAL_CONTENT
    if _is_artifact_request(message):
        return ChatIntent.OUT_OF_SCOPE_ARTIFACT_REQUEST
    return ChatIntent.REPORT_ANALYSIS


def _build_disallowed_request_response() -> str:
    return _REFUSAL_LINE


def _build_artifact_request_response() -> str:
    return (
        "I can only analyze and explain this report in chat. "
        "I cannot generate files, exports, or downloadable links."
    )


def _build_missing_data_follow_up(message: str) -> str:
    normalized = _normalize_safety_text(message)
    if any(term in normalized for term in ("union", "collective bargaining", "unionized")):
        return "Would you like me to look at the hiring pressure and labor signals that are actually shown in the report?"
    if any(term in normalized for term in ("turnover", "attrition", "retention", "tenure")):
        return "Would you like me to break down the hiring urgency and chronically open roles that the report does show?"
    if any(term in normalized for term in ("recruit", "hiring", "warehouse", "labor", "workforce")):
        return "Would you like me to look at which roles appear most chronically open in the report?"
    if any(term in normalized for term in ("cost", "roi", "save")):
        return "Would you like me to look at the labor-cost signals and growth indicators that are available in the report?"
    if any(term in normalized for term in ("safety", "injur", "strain")):
        return "Would you like me to look at the manual-work and dockside-risk signals that are available in the report?"
    return "Would you like me to look at the closest related report signal that is available?"


def _ensure_missing_data_follow_up(reply: str, message: str) -> str:
    text = str(reply or "").strip()
    if not text:
        return text
    if "?" in text:
        return text
    if not _MISSING_DATA_REPLY_PATTERN.search(text):
        return text
    return f"{text}\n\n{_build_missing_data_follow_up(message)}"


def _looks_like_disallowed_output(reply: str) -> bool:
    normalized = _normalize_reply_text(reply)
    return bool(_DISALLOWED_OUTPUT_PATTERN.search(normalized))


def _sanitize_artifact_drift(reply: str) -> str:
    text = str(reply or "").strip()
    if not text:
        return text

    lines = text.splitlines()
    cleaned_lines: list[str] = []
    in_artifact_block = False
    removed_any = False

    for raw_line in lines:
        line = raw_line.strip()

        # Drop unsolicited CSV code blocks entirely if they appear in analysis mode.
        if line.lower().startswith("```csv"):
            in_artifact_block = True
            removed_any = True
            continue
        if in_artifact_block:
            if line.startswith("```"):
                in_artifact_block = False
            removed_any = True
            continue

        if _ARTIFACT_OFFER_PATTERN.search(line) and (
            "would you like" in line.lower()
            or "if you want" in line.lower()
            or "i can" in line.lower()
            or "download" in line.lower()
            or "export" in line.lower()
            or "import" in line.lower()
        ):
            removed_any = True
            continue

        cleaned_lines.append(raw_line)

    cleaned = "\n".join(cleaned_lines).strip()
    if cleaned:
        return cleaned

    if removed_any:
        return (
            "That information is not available in this report. "
            "I can continue analyzing relevant report details directly in chat if helpful."
        )
    return text


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
        {"role": "system", "content": SYSTEM_PROMPT_TEMPLATE.format(
            report_context=report_context
        )},
        *[{"role": m["role"], "content": m["content"]} for m in llm_window],
    ]

    intent = _classify_intent(message)
    if intent == ChatIntent.OUT_OF_SCOPE_EXTERNAL_CONTENT:
        reply_text = _build_disallowed_request_response()
    elif intent == ChatIntent.OUT_OF_SCOPE_ARTIFACT_REQUEST:
        reply_text = _build_artifact_request_response()
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
                reply_text = _build_disallowed_request_response()
            else:
                reply_text = _sanitize_artifact_drift(reply_text)
                reply_text = _ensure_missing_data_follow_up(reply_text, message)
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
