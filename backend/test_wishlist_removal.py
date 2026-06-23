import pytest

import backend.main as main


class FakeSupabaseAdmin:
    def __init__(self):
        self.delete_calls: list[dict] = []

    async def request(self, method, path, *, params=None, json_body=None, headers=None):
        if method == "DELETE" and path == "/rest/v1/automatisor_customer_context":
            self.delete_calls.append({"params": params or {}, "headers": headers or {}})
            return None
        raise AssertionError(f"Unexpected request: {method} {path}")


@pytest.mark.asyncio
async def test_remove_customer_wishlist_item_deletes_matching_row():
    db = FakeSupabaseAdmin()
    removed = await main.remove_customer_wishlist_item(db, "customer-1", "site-1")
    assert removed is True
    assert len(db.delete_calls) == 1
    params = db.delete_calls[0]["params"]
    assert params["customer_id"] == "eq.customer-1"
    assert params["site_id"] == "eq.site-1"
    assert params["event_type"] == f"eq.{main.EVENT_TYPE_WISH_LIST}"


@pytest.mark.asyncio
async def test_remove_customer_wishlist_item_returns_false_without_ids():
    db = FakeSupabaseAdmin()
    assert await main.remove_customer_wishlist_item(db, "", "site-1") is False
    assert await main.remove_customer_wishlist_item(db, "customer-1", "") is False
    assert db.delete_calls == []
