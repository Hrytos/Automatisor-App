"""End-to-end share feature tests: sender share, recipient existing/new user flows."""

import pytest
from fastapi import HTTPException

import backend.main as main


@pytest.fixture
def share_secret(monkeypatch):
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")
    monkeypatch.setattr(main, "APP_BASE_URL", "https://example.com")
    return True


async def _async_return(value):
    return value


def _patch_async(monkeypatch, name, value):
    async def _fn(*args, **kwargs):
        if callable(value):
            result = value(*args, **kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result
        return value

    monkeypatch.setattr(main, name, _fn)


def _encode_token(recipient: str, site_id: str, sharer: str) -> str:
    return main.encode_share_token(recipient, site_id, sharer)


class FakeRequest:
    cookies: dict = {}
    query_params: dict = {}


# ── Sender: POST /api/reports/share ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_share_report_new_recipient_only_sends_email(share_secret, monkeypatch):
    """Unregistered recipient: no auto-sync row, email still sent."""
    sender = {"customer_id": "sender-1", "email": "sender@company.com"}
    assignment = {
        "customer_site_id": "cs-sender-1",
        "site_id": "site-1",
        "is_report_ready": True,
        "account_id": "acct-1",
    }
    sync_calls: list[str] = []
    emails_sent: list[str] = []

    async def fake_find_customer_by_email(db, email):
        return None

    async def fake_ensure_shared_site_assignment(*args, **kwargs):
        sync_calls.append("called")
        raise AssertionError("should not auto-sync unregistered recipient")

    async def fake_send_report_share_email(recipient, *args):
        emails_sent.append(recipient)

    class FakeDb:
        async def request(self, *args, **kwargs):
            return [{"site_id": "site-1", "company_name": "Acme", "full_address": "1 Main St"}]

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())
    _patch_async(monkeypatch, "get_authenticated_customer", sender)
    _patch_async(monkeypatch, "resolve_sender_report_assignment", assignment)
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)
    monkeypatch.setattr(main, "ensure_shared_site_assignment", fake_ensure_shared_site_assignment)
    monkeypatch.setattr(main, "send_report_share_email", fake_send_report_share_email)

    response = await main.share_report(
        FakeRequest(),
        {"customer_site_id": "cs-sender-1", "site_id": "site-1", "emails": ["newuser@company.com"]},
    )

    assert response["sent"] == 1
    assert emails_sent == ["newuser@company.com"]
    assert sync_calls == []


@pytest.mark.asyncio
async def test_share_report_existing_recipient_auto_syncs_and_sends_email(share_secret, monkeypatch):
    """Registered recipient: auto-sync creates row, email sent."""
    sender = {"customer_id": "sender-1", "email": "sender@company.com"}
    assignment = {
        "customer_site_id": "cs-sender-1",
        "site_id": "site-1",
        "is_report_ready": True,
        "account_id": "acct-1",
    }
    sync_calls: list[tuple] = []
    emails_sent: list[str] = []
    created_row = {
        "customer_site_id": "cs-recipient-1",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "shared_site",
        "shared_by": "sender-1",
    }

    async def fake_find_customer_by_email(db, email):
        if email == "existing@company.com":
            return {"customer_id": "recipient-1", "email": email}
        return None

    async def fake_ensure_shared_site_assignment(db, customer_id, site_id, shared_by, **kwargs):
        sync_calls.append((customer_id, site_id, shared_by))
        return created_row

    async def fake_send_report_share_email(recipient, *args):
        emails_sent.append(recipient)

    class FakeDb:
        async def request(self, *args, **kwargs):
            return [{"site_id": "site-1", "company_name": "Acme", "full_address": "1 Main St"}]

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())
    _patch_async(monkeypatch, "get_authenticated_customer", sender)
    _patch_async(monkeypatch, "resolve_sender_report_assignment", assignment)
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)
    monkeypatch.setattr(main, "ensure_shared_site_assignment", fake_ensure_shared_site_assignment)
    monkeypatch.setattr(main, "send_report_share_email", fake_send_report_share_email)

    response = await main.share_report(
        FakeRequest(),
        {"customer_site_id": "cs-sender-1", "site_id": "site-1", "emails": ["existing@company.com"]},
    )

    assert response["sent"] == 1
    assert sync_calls == [("recipient-1", "site-1", "sender-1")]
    assert emails_sent == ["existing@company.com"]


@pytest.mark.asyncio
async def test_share_report_rejects_unready_report(share_secret, monkeypatch):
    sender = {"customer_id": "sender-1", "email": "sender@company.com"}
    assignment = {"customer_site_id": "cs-1", "site_id": "site-1", "is_report_ready": False}

    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    _patch_async(monkeypatch, "get_authenticated_customer", sender)
    _patch_async(monkeypatch, "resolve_sender_report_assignment", assignment)

    with pytest.raises(HTTPException) as exc:
        await main.share_report(
            FakeRequest(),
            {"customer_site_id": "cs-1", "emails": ["other@company.com"]},
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "Only ready reports can be shared."


# ── Share link: POST /api/share/resolve ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_share_token_returns_site_details(share_secret, monkeypatch):
    token = _encode_token("recipient@company.com", "site-1", "sender-1")

    class FakeDb:
        async def request(self, method, path, params=None, **kwargs):
            return [{"site_id": "site-1", "company_name": "Acme Corp", "full_address": "1 Main St"}]

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())

    response = await main.resolve_share_token({"share_token": token})

    assert response["status"] == "valid"
    assert response["recipient_email"] == "recipient@company.com"
    assert response["site_id"] == "site-1"
    assert response["company_name"] == "Acme Corp"


@pytest.mark.asyncio
async def test_resolve_share_token_rejects_invalid_token(share_secret):
    with pytest.raises(HTTPException) as exc:
        await main.resolve_share_token({"share_token": "not.valid.token"})
    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid share link."


@pytest.mark.asyncio
async def test_resolve_share_token_rejects_missing_site(share_secret, monkeypatch):
    token = _encode_token("recipient@company.com", "gone-site", "sender-1")

    class FakeDb:
        async def request(self, *args, **kwargs):
            return []

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())

    with pytest.raises(HTTPException) as exc:
        await main.resolve_share_token({"share_token": token})
    assert exc.value.status_code == 404


# ── Recipient OTP: POST /api/auth/verify-otp ────────────────────────────────


@pytest.mark.asyncio
async def test_verify_otp_existing_user_with_share_creates_assignment(share_secret, monkeypatch):
    """Existing user opens share link, verifies OTP → lands on shared report."""
    token = _encode_token("existing@company.com", "site-1", "sender-1")
    shared_row = {
        "customer_site_id": "cs-recipient-1",
        "site_id": "site-1",
        "account_id": "acct-1",
    }
    ensure_calls: list[tuple] = []

    async def fake_verify_supabase_otp(email, otp):
        return {"session": {"access_token": "tok"}, "user": {"id": "uid-1"}}

    async def fake_find_customer_by_email(db, email):
        return {"customer_id": "recipient-1", "email": email}

    async def fake_ensure_shared_site_assignment(db, customer_id, site_id, shared_by):
        ensure_calls.append((customer_id, site_id, shared_by))
        return shared_row

    async def fake_build_workspace_payload(db, email):
        return {"email": email, "next_step": "workspace", "sites": []}

    monkeypatch.setattr(main, "verify_supabase_otp", fake_verify_supabase_otp)
    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)
    _patch_async(monkeypatch, "mark_customer_verified", None)
    _patch_async(monkeypatch, "touch_customer_login", None)
    monkeypatch.setattr(main, "ensure_shared_site_assignment", fake_ensure_shared_site_assignment)
    monkeypatch.setattr(main, "build_workspace_payload", fake_build_workspace_payload)

    from starlette.responses import Response

    response = Response()
    payload = await main.handle_verify_otp(
        response,
        FakeRequest(),
        {"email": "existing@company.com", "otp": "123456", "share_token": token},
    )

    assert payload["status"] == "verified"
    assert payload["share_destination"] == shared_row
    assert ensure_calls == [("recipient-1", "site-1", "sender-1")]


@pytest.mark.asyncio
async def test_verify_otp_new_user_with_share_defers_to_onboarding(share_secret, monkeypatch):
    """New user opens share link, verifies OTP → step 1 onboarding with token preserved."""
    token = _encode_token("newuser@company.com", "site-1", "sender-1")

    async def fake_verify_supabase_otp(email, otp):
        return {"session": {"access_token": "tok"}, "user": {"id": "uid-2"}}

    async def fake_find_customer_by_email(db, email):
        return None

    monkeypatch.setattr(main, "verify_supabase_otp", fake_verify_supabase_otp)
    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)

    from starlette.responses import Response

    response = Response()
    payload = await main.handle_verify_otp(
        response,
        FakeRequest(),
        {"email": "newuser@company.com", "otp": "123456", "share_token": token},
    )

    assert payload["status"] == "verified"
    assert payload["user_mode"] == "new_user"
    assert payload["next_step"] == "onboarding_step1"
    assert payload["share_token"] == token
    assert payload["customer_id"] is None


@pytest.mark.asyncio
async def test_verify_otp_rejects_wrong_recipient_email_on_share_link(share_secret, monkeypatch):
    """Share token bound to recipient@ — other@ cannot use it."""
    token = _encode_token("recipient@company.com", "site-1", "sender-1")

    async def fake_verify_supabase_otp(email, otp):
        return {"session": {"access_token": "tok"}, "user": {"id": "uid-3"}}

    async def fake_find_customer_by_email(db, email):
        return {"customer_id": "other-1", "email": email}

    async def fake_build_workspace_state(db, email):
        return {"email": email, "next_step": "workspace"}

    monkeypatch.setattr(main, "verify_supabase_otp", fake_verify_supabase_otp)
    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)
    _patch_async(monkeypatch, "mark_customer_verified", None)
    _patch_async(monkeypatch, "touch_customer_login", None)
    monkeypatch.setattr(main, "build_workspace_state", fake_build_workspace_state)

    from starlette.responses import Response

    response = Response()
    payload = await main.handle_verify_otp(
        response,
        FakeRequest(),
        {"email": "other@company.com", "otp": "123456", "share_token": token},
    )

    # Share token email mismatch → normal existing-user workspace flow, no share_destination
    assert "share_destination" not in payload
    assert payload["next_step"] == "workspace"


# ── New user onboarding: POST /api/onboarding/step1 ───────────────────────────


@pytest.mark.asyncio
async def test_onboarding_step1_new_share_recipient_creates_assignment(share_secret, monkeypatch):
    """New share recipient completes step 1 → shared report row created, navigates to report."""
    token = _encode_token("newuser@company.com", "site-1", "sender-1")
    shared_row = {
        "customer_site_id": "cs-new-1",
        "site_id": "site-1",
        "account_id": "acct-1",
    }
    ensure_calls: list[tuple] = []

    async def fake_upsert_customer(db, body):
        return {"customerId": "new-customer-1", "email": body["email"]}

    async def fake_ensure_shared_site_assignment(db, customer_id, site_id, shared_by):
        ensure_calls.append((customer_id, site_id, shared_by))
        return shared_row

    async def fake_build_workspace_payload(db, email):
        return {"email": email, "sites": [shared_row]}

    class FakeDb:
        async def request(self, *args, **kwargs):
            return []

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())
    monkeypatch.setattr(main, "upsert_customer", fake_upsert_customer)
    _patch_async(monkeypatch, "mark_customer_verified", None)
    _patch_async(monkeypatch, "create_stripe_customer", "stripe-1")
    monkeypatch.setattr(main, "ensure_shared_site_assignment", fake_ensure_shared_site_assignment)
    monkeypatch.setattr(main, "build_workspace_payload", fake_build_workspace_payload)

    payload = await main.handle_onboarding_step1(
        FakeRequest(),
        {
            "email": "newuser@company.com",
            "first_name": "New",
            "last_name": "User",
            "customer_company_name": "New Co",
            "terms_accepted": True,
            "share_token": token,
        },
    )

    assert payload["status"] == "step1_complete"
    assert payload["next_step"] == "share_destination"
    assert payload["share_destination"] == shared_row
    assert ensure_calls == [("new-customer-1", "site-1", "sender-1")]


@pytest.mark.asyncio
async def test_onboarding_step1_existing_synced_share_skips_duplicate_create(share_secret, monkeypatch):
    """Share recipient who was auto-synced on email send uses existing row at step 1."""
    token = _encode_token("existing@company.com", "site-1", "sender-1")
    existing_row = {
        "customer_site_id": "cs-existing-1",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "shared_site",
        "shared_by": "sender-1",
    }
    ensure_calls: list[str] = []

    async def fake_upsert_customer(db, body):
        return {"customerId": "recipient-1", "email": body["email"]}

    async def fake_ensure_shared_site_assignment(*args, **kwargs):
        ensure_calls.append("should-not-run")
        raise AssertionError("row already exists")

    async def fake_build_workspace_payload(db, email):
        return {"email": email, "sites": [existing_row]}

    class FakeDb:
        async def request(self, *args, **kwargs):
            if kwargs.get("params", {}).get("assigned_via") == "eq.shared_site":
                return [existing_row]
            return []

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())
    monkeypatch.setattr(main, "upsert_customer", fake_upsert_customer)
    _patch_async(monkeypatch, "mark_customer_verified", None)
    _patch_async(monkeypatch, "create_stripe_customer", "stripe-1")
    monkeypatch.setattr(main, "ensure_shared_site_assignment", fake_ensure_shared_site_assignment)
    monkeypatch.setattr(main, "build_workspace_payload", fake_build_workspace_payload)

    payload = await main.handle_onboarding_step1(
        FakeRequest(),
        {
            "email": "existing@company.com",
            "first_name": "Exist",
            "last_name": "User",
            "customer_company_name": "Exist Co",
            "terms_accepted": True,
            "share_token": token,
        },
    )

    assert payload["next_step"] == "share_destination"
    assert payload["share_destination"]["customer_site_id"] == "cs-existing-1"
    assert ensure_calls == []


# ── ensure_shared_site_assignment idempotency ─────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_shared_site_assignment_returns_existing_without_insert(monkeypatch):
    existing = {
        "customer_site_id": "cs-1",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "shared_site",
        "shared_by": "sender-1",
        "is_report_ready": True,
        "report_metadata": {"sections": []},
    }
    post_calls: list[str] = []

    async def fake_request(method, path, params=None, json_body=None, **kwargs):
        if method == "GET" and params and params.get("shared_by") == "eq.sender-1":
            return [existing]
        if method == "POST":
            post_calls.append("insert")
        return []

    async def fake_find_share_source(*args, **kwargs):
        return {
            "site_id": "site-1",
            "account_id": "acct-1",
            "is_report_ready": True,
            "report_metadata": {},
            "recommendations": {},
            "metadata": {},
        }

    class FakeDb:
        request = staticmethod(fake_request)

    monkeypatch.setattr(main, "find_share_source_assignment", fake_find_share_source)

    result = await main.ensure_shared_site_assignment(
        FakeDb(), "recipient-1", "site-1", "sender-1", source_customer_site_id="cs-sender-1"
    )

    assert result == existing
    assert post_calls == []
