import pytest

import backend.main as main


def test_chat_share_token_round_trip(monkeypatch):
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")
    token = main.encode_share_token(
        "recipient@company.com",
        "site-123",
        "sharer-456",
        session_id="session-789",
        share_type="chat",
    )
    payload = main.decode_share_token(token)
    assert payload is not None
    assert payload["recipient_email"] == "recipient@company.com"
    assert payload["site_id"] == "site-123"
    assert payload["shared_by"] == "sharer-456"
    assert payload["share_type"] == "chat"
    assert payload["session_id"] == "session-789"


def test_report_share_token_still_defaults_to_report(monkeypatch):
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")
    token = main.encode_share_token("recipient@company.com", "site-123", "sharer-456")
    payload = main.decode_share_token(token)
    assert payload is not None
    assert payload.get("share_type") == "report"
    assert "session_id" not in payload


def test_snapshot_chat_messages_deep_copies():
    source = [{"role": "user", "content": "hello", "metadata": {"x": 1}}]
    copied = main.snapshot_chat_messages(source)
    assert copied == source
    assert copied is not source
    copied[0]["content"] = "changed"
    assert source[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_fulfill_pending_chat_shares_updates_rows(monkeypatch):
    captured: dict[str, object] = {}

    class FakeDb:
        async def request(self, method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            captured["kwargs"] = kwargs
            if method == "GET":
                return [{"session_id": "pending-1"}]
            return []

    await main.fulfill_pending_chat_shares(FakeDb(), "recipient@company.com", "cust-1")
    assert captured["method"] == "PATCH"
    assert captured["kwargs"]["json_body"]["customer_id"] == "cust-1"
    assert captured["kwargs"]["json_body"]["pending_recipient_email"] is None


def test_build_chat_share_email_includes_conversation_title():
    html = main.build_chat_share_email(
        "sender@company.com",
        "Acme DC",
        "123 Main St",
        "How to position my solution",
        "https://app.example/auth?share=token",
    )
    assert "Conversation shared with you" in html
    assert "How to position my solution" in html
    assert "View conversation" in html
