import pytest

import backend.main as main


def test_shared_snapshot_needs_backfill_when_context_missing():
    assert main._shared_snapshot_needs_backfill(
        {
            "is_report_ready": True,
            "report_metadata": {"sections": []},
            "report_context_high": {},
            "report_context_all": {"summary": "full"},
        }
    )
    assert main._shared_snapshot_needs_backfill(
        {
            "is_report_ready": True,
            "report_metadata": {"sections": []},
            "report_context_high": {"summary": "high"},
            "report_context_all": {},
        }
    )
    assert not main._shared_snapshot_needs_backfill(
        {
            "is_report_ready": True,
            "report_metadata": {"sections": []},
            "report_context_high": {"summary": "high"},
            "report_context_all": {"summary": "full"},
        }
    )


def test_is_share_source_row_requires_matching_site_and_ready_flag():
    assert main._is_share_source_row(
        {"site_id": "site-1", "is_report_ready": True},
        "site-1",
    )
    assert not main._is_share_source_row(
        {"site_id": "site-1", "is_report_ready": False},
        "site-1",
    )
    assert not main._is_share_source_row(
        {"site_id": "site-2", "is_report_ready": True},
        "site-1",
    )
    assert not main._is_share_source_row(None, "site-1")


@pytest.mark.asyncio
async def test_find_share_source_prefers_explicit_sharer_row(monkeypatch):
    shared_snapshot = {
        "customer_site_id": "shared-row-9",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "shared_site",
        "is_report_ready": True,
        "report_metadata": {"sections": []},
        "recommendations": {"status": "ready"},
        "metadata": {},
    }

    async def fake_find_customer_site_assignment(db, customer_id, customer_site_id):
        assert customer_id == "bob"
        assert customer_site_id == "shared-row-9"
        return shared_snapshot

    async def fail_request(*_args, **_kwargs):
        raise AssertionError("Should not query fallback sources when explicit row is valid")

    monkeypatch.setattr(main, "find_customer_site_assignment", fake_find_customer_site_assignment)

    class FakeDb:
        request = staticmethod(fail_request)

    result = await main.find_share_source_assignment(
        FakeDb(),
        "site-1",
        "bob",
        source_customer_site_id="shared-row-9",
    )
    assert result == shared_snapshot


@pytest.mark.asyncio
async def test_find_share_source_falls_back_to_sharer_shared_row(monkeypatch):
    shared_snapshot = {
        "customer_site_id": "shared-row-9",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "shared_site",
        "is_report_ready": True,
        "report_metadata": {"sections": []},
        "recommendations": {"status": "ready"},
        "metadata": {},
    }
    calls: list[dict] = []

    async def fake_request(_method, _path, params=None, **_kwargs):
        calls.append(params or {})
        if params and params.get("assigned_via") == "in.(user_added_site,dev_added_site)":
            return []
        if params and params.get("assigned_via") == "eq.shared_site":
            return [shared_snapshot]
        return []

    class FakeDb:
        request = staticmethod(fake_request)

    result = await main.find_share_source_assignment(FakeDb(), "site-1", "bob")
    assert result == shared_snapshot
    assert len(calls) == 2
    assert calls[0]["customer_id"] == "eq.bob"
    assert calls[1]["assigned_via"] == "eq.shared_site"


@pytest.mark.asyncio
async def test_find_share_source_never_queries_other_customers_when_sharer_known(monkeypatch):
    calls: list[dict] = []

    async def fake_request(_method, _path, params=None, **_kwargs):
        calls.append(params or {})
        return []

    class FakeDb:
        request = staticmethod(fake_request)

    result = await main.find_share_source_assignment(FakeDb(), "site-1", "bob")
    assert result is None
    assert len(calls) == 2
    assert all(call.get("customer_id") == "eq.bob" for call in calls)


@pytest.mark.asyncio
async def test_ensure_shared_site_assignment_copies_report_context_columns(monkeypatch):
    source_row = {
        "customer_site_id": "cs-sender-1",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "user_added_site",
        "is_report_ready": True,
        "report_metadata": {"sections": []},
        "report_context_high": {"headline": "high"},
        "report_context_all": {"headline": "all"},
        "recommendations": {"status": "ready"},
        "metadata": {},
    }
    created_body: dict = {}

    async def fake_find_share_source_assignment(db, site_id, shared_by_customer_id, **kwargs):
        assert kwargs.get("source_customer_site_id") == "cs-sender-1"
        return source_row

    async def fake_request(method, path, params=None, json_body=None, **_kwargs):
        if method == "GET" and path == "/rest/v1/automatisor_customer_sites":
            return []
        if method == "POST" and path == "/rest/v1/automatisor_customer_sites":
            created_body.update(json_body or {})
            return [
                {
                    "customer_site_id": "cs-recipient-1",
                    "site_id": "site-1",
                    "account_id": "acct-1",
                    "assigned_via": "shared_site",
                    "shared_by": "sender-1",
                }
            ]
        raise AssertionError(f"Unexpected request: {method} {path}")

    monkeypatch.setattr(main, "find_share_source_assignment", fake_find_share_source_assignment)

    class FakeDb:
        request = staticmethod(fake_request)

    result = await main.ensure_shared_site_assignment(
        FakeDb(),
        "recipient-1",
        "site-1",
        "sender-1",
        source_customer_site_id="cs-sender-1",
    )

    assert result["customer_site_id"] == "cs-recipient-1"
    assert created_body["report_context_high"] == {"headline": "high"}
    assert created_body["report_context_all"] == {"headline": "all"}
