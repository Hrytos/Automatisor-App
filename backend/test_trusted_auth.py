"""Trusted allowlist auth bypass tests."""

import pytest
from fastapi import HTTPException

import backend.main as main


class FakeRequest:
    cookies: dict = {}
    query_params: dict = {}


@pytest.fixture
def trusted_email(monkeypatch):
    monkeypatch.setattr(main, "TRUSTED_AUTH_BYPASS_EMAILS", {"jason.huey@vecnarobotics.com"})
    return "jason.huey@vecnarobotics.com"


@pytest.mark.asyncio
async def test_check_email_marks_trusted_bypass(trusted_email, monkeypatch):
    async def fake_find_customer_by_email(db, email):
        return {"customer_id": "cust-1", "email": email}

    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)

    payload = await main.handle_check_email({"email": trusted_email})

    assert payload["trusted_bypass"] is True
    assert payload["user_mode"] == "existing_user"


@pytest.mark.asyncio
async def test_request_otp_skips_send_for_trusted_email(trusted_email, monkeypatch):
    send_calls: list[str] = []

    async def fake_send_supabase_otp(email):
        send_calls.append(email)

    async def fake_find_customer_by_email(db, email):
        return {"customer_id": "cust-1", "email": email}

    monkeypatch.setattr(main, "send_supabase_otp", fake_send_supabase_otp)
    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)

    payload = await main.handle_request_otp(FakeRequest(), {"email": trusted_email})

    assert payload["status"] == "trusted_bypass"
    assert payload["trusted_bypass"] is True
    assert send_calls == []


@pytest.mark.asyncio
async def test_trusted_login_rejects_non_allowlisted_email(monkeypatch):
    monkeypatch.setattr(main, "TRUSTED_AUTH_BYPASS_EMAILS", {"trusted@company.com"})
    from starlette.responses import Response

    with pytest.raises(HTTPException) as exc:
        await main.handle_trusted_login(
            Response(),
            FakeRequest(),
            {"email": "other@company.com"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_trusted_login_sets_cookie_and_returns_workspace(trusted_email, monkeypatch):
    async def fake_mint_supabase_session_for_email(email):
        return {"session": {"access_token": "trusted-token"}, "user": {"id": "uid-1"}}

    async def fake_find_customer_by_email(db, email):
        return {"customer_id": "cust-1", "email": email}

    async def fake_build_workspace_state(db, email):
        return {"email": email, "user_mode": "existing_user", "next_step": "workspace"}

    async def fake_mark_customer_verified(db, email):
        return None

    async def fake_touch_customer_login(db, customer_id):
        return None

    monkeypatch.setattr(main, "mint_supabase_session_for_email", fake_mint_supabase_session_for_email)
    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)
    monkeypatch.setattr(main, "mark_customer_verified", fake_mark_customer_verified)
    monkeypatch.setattr(main, "touch_customer_login", fake_touch_customer_login)
    monkeypatch.setattr(main, "build_workspace_state", fake_build_workspace_state)

    from starlette.responses import Response

    response = Response()
    payload = await main.handle_trusted_login(
        response,
        FakeRequest(),
        {"email": trusted_email},
    )

    assert payload["status"] == "verified"
    assert payload["next_step"] == "workspace"
    assert response.headers.get("set-cookie", "").startswith("access_token=trusted-token")
