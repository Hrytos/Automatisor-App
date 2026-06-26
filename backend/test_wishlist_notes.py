import pytest

import backend.main as main


class FakeSupabaseAdmin:
    def __init__(self, site_row=None, wishlist_notes=""):
        self.site_row = site_row or {"customer_site_id": "cs-1", "notes": ""}
        self.wishlist_notes = wishlist_notes
        self.patch_calls: list[dict] = []

    async def request(self, method, path, *, params=None, json_body=None, headers=None):
        if method == "GET" and path == "/rest/v1/automatisor_customer_sites":
            return [self.site_row] if self.site_row else []
        if method == "GET" and path == "/rest/v1/automatisor_customer_context":
            if self.wishlist_notes:
                return [{"notes": self.wishlist_notes}]
            return []
        if method == "PATCH" and path == "/rest/v1/automatisor_customer_sites":
            self.patch_calls.append({"params": params or {}, "json_body": json_body or {}})
            return None
        raise AssertionError(f"Unexpected request: {method} {path} {params}")


@pytest.mark.asyncio
async def test_apply_wishlist_notes_to_customer_site_patches_when_facility_notes_empty():
    db = FakeSupabaseAdmin(site_row={"customer_site_id": "cs-1", "notes": ""})
    await main.apply_wishlist_notes_to_customer_site(db, "cs-1", "Wishlist note text")
    assert len(db.patch_calls) == 1
    assert db.patch_calls[0]["json_body"]["notes"] == "Wishlist note text"


@pytest.mark.asyncio
async def test_apply_wishlist_notes_to_customer_site_skips_when_facility_has_notes():
    db = FakeSupabaseAdmin(site_row={"customer_site_id": "cs-1", "notes": "Existing facility note"})
    await main.apply_wishlist_notes_to_customer_site(db, "cs-1", "Wishlist note text")
    assert db.patch_calls == []


@pytest.mark.asyncio
async def test_get_wishlist_notes_for_site_reads_wishlist_row():
    db = FakeSupabaseAdmin(wishlist_notes="Saved on wishlist")
    notes = await main.get_wishlist_notes_for_site(db, "customer-1", "site-1")
    assert notes == "Saved on wishlist"


@pytest.mark.asyncio
async def test_request_pre_assessment_delegates_to_submit_helper(monkeypatch):
    captured: dict = {}

    async def fake_submit(db, *, request, body, customer, email, account_id, site_id, customer_site_id=None):
        captured.update(
            {
                "account_id": account_id,
                "site_id": site_id,
                "customer_site_id": customer_site_id,
                "email": email,
            }
        )
        return {
            "status": "running",
            "account_id": account_id,
            "site_id": site_id,
            "message": "Pre-assessment request approved.",
        }

    async def fake_find_customer(db, email):
        return {"customer_id": "customer-1", "email": email}

    async def fake_list_accounts(db, customer):
        return [{"account_id": "account-1", "company_name": "Acme"}]

    def fake_choose_active(accounts, requested_account_id):
        return accounts[0]

    monkeypatch.setattr(main, "_submit_pre_assessment_request", fake_submit)
    monkeypatch.setattr(main, "get_admin_db", lambda: object())
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer)
    monkeypatch.setattr(main, "list_customer_accounts", fake_list_accounts)
    monkeypatch.setattr(main, "choose_active_account", fake_choose_active)

    from starlette.requests import Request

    scope = {"type": "http", "method": "POST", "headers": [], "path": "/api/pre-assessment/request"}
    request = Request(scope)
    result = await main.request_pre_assessment(
        request,
        {
            "email": "user@company.com",
            "account_id": "account-1",
            "site_id": "site-1",
            "customer_site_id": "cs-1",
            "confirmed": True,
        },
    )

    assert captured["account_id"] == "account-1"
    assert captured["site_id"] == "site-1"
    assert captured["customer_site_id"] == "cs-1"
    assert result["email"] == "user@company.com"
    assert result["pre_assessment_price_credits"] == main.PRE_ASSESSMENT_PRICE
