import pytest
from typing import Any

import backend.main as main


class FakeSupabaseAdmin:
    def __init__(self, recommendations: dict[str, Any] | None = None):
        self.patch_calls = []
        self.recommendations = recommendations if recommendations is not None else {}

    async def request(self, method, path, *, params=None, json_body=None, headers=None):
        if method == "GET" and path == "/rest/v1/automatisor_customer_sites":
            return [{"recommendations": self.recommendations}]
        if method == "PATCH" and path == "/rest/v1/automatisor_customer_sites":
            self.patch_calls.append({"params": params or {}, "json_body": json_body or {}})
            return []
        raise AssertionError(f"Unexpected request: {method} {path}")


def test_should_enqueue_site_recommendations_skips_terminal_states():
    assert main.should_enqueue_site_recommendations({"recommendations": {"status": "running"}}) is False
    assert main.should_enqueue_site_recommendations({"recommendations": {"status": "review"}}) is False
    assert main.should_enqueue_site_recommendations({"recommendations": {"status": "ready"}}) is False
    assert main.should_enqueue_site_recommendations({"recommendations": {}}) is True
    assert main.should_enqueue_site_recommendations({"recommendations": {"status": "failed"}}) is True


@pytest.mark.asyncio
async def test_mark_site_recommendations_running_patches_row():
    db = FakeSupabaseAdmin()
    await main.mark_site_recommendations_running(db, "site-assignment-1")
    assert len(db.patch_calls) == 1
    assert db.patch_calls[0]["params"]["customer_site_id"] == "eq.site-assignment-1"
    assert db.patch_calls[0]["json_body"]["recommendations"]["status"] == "running"


@pytest.mark.asyncio
async def test_maybe_start_site_recommendations_enqueues_when_idle(monkeypatch):
    db = FakeSupabaseAdmin()
    scheduled: list[str] = []

    monkeypatch.setattr(main, "schedule_site_recommendations_on_worker", lambda customer_site_id: scheduled.append(customer_site_id))

    await main.maybe_start_site_recommendations(
        db,
        {"recommendations": {}},
        assignment_customer_site_id="site-assignment-1",
    )

    assert scheduled == ["site-assignment-1"]
    assert db.patch_calls[0]["json_body"]["recommendations"]["status"] == "running"


@pytest.mark.asyncio
async def test_maybe_start_site_recommendations_skips_when_ready(monkeypatch):
    db = FakeSupabaseAdmin(recommendations={"status": "ready"})
    scheduled: list[str] = []

    monkeypatch.setattr(main, "schedule_site_recommendations_on_worker", lambda customer_site_id: scheduled.append(customer_site_id))

    await main.maybe_start_site_recommendations(
        db,
        {"recommendations": {}},
        assignment_customer_site_id="site-assignment-1",
    )

    assert scheduled == []
    assert db.patch_calls == []
