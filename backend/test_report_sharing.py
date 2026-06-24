import pytest

import backend.main as main


def test_share_token_round_trip(monkeypatch):
    """Test that share tokens can be encoded and decoded correctly."""
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")

    token = main.encode_share_token("Recipient@Company.com", "site-123", "sharer-456")
    payload = main.decode_share_token(token)

    assert payload == {
        "recipient_email": "recipient@company.com",
        "site_id": "site-123",
        "shared_by": "sharer-456",
        "share_type": "report",
    }


def test_share_token_rejects_tampering(monkeypatch):
    """Test that tampered tokens are rejected."""
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")

    token = main.encode_share_token("recipient@company.com", "site-123", "sharer-456")
    encoded, signature = token.split(".", 1)
    tampered = f"{encoded[:-1]}x.{signature}"

    assert main.decode_share_token(tampered) is None


def test_share_token_returns_none_without_secret(monkeypatch):
    """Test that tokens cannot be decoded without secret."""
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "")
    monkeypatch.delenv("SHARE_TOKEN_SECRET", raising=False)

    assert main.decode_share_token("not-a-token") is None


def test_share_token_rejects_malformed_base64(monkeypatch):
    """Test that malformed tokens are rejected."""
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")

    assert main.decode_share_token("@@@.%%%") is None


def test_share_token_backward_compat_without_shared_by(monkeypatch):
    """Test that old tokens without shared_by still work (backward compatibility)."""
    import json
    import base64
    import hmac
    import hashlib
    
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")
    monkeypatch.setenv("SHARE_TOKEN_SECRET", "test-secret")
    payload = json.dumps(
        {"recipient_email": "recipient@company.com", "site_id": "site-123"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new("test-secret".encode("utf-8"), encoded, hashlib.sha256).digest()
    token = f"{encoded.decode('ascii')}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"
    
    result = main.decode_share_token(token)
    assert result == {
        "recipient_email": "recipient@company.com",
        "site_id": "site-123",
        "shared_by": "",  # Empty string for old tokens
        "share_type": "report",
    }


def test_validate_share_recipients_reports_all_errors():
    """Test that recipient validation catches all error types."""
    valid, errors = main.validate_share_recipients(
        [
            "valid@company.com",
            "bad-email",
            "person@gmail.com",
            "sender@company.com",
            "VALID@company.com",
        ],
        "sender@company.com",
    )

    assert valid == ["valid@company.com"]
    assert errors == [
        {"email": "bad-email", "reason": "Invalid work email"},
        {"email": "person@gmail.com", "reason": "Please use your work email address"},
        {"email": "sender@company.com", "reason": "You cannot share a report with yourself"},
        {"email": "valid@company.com", "reason": "Duplicate address"},
    ]


@pytest.mark.asyncio
async def test_share_report_resends_email_without_creating_duplicate_row(monkeypatch):
    """Re-sharing to an existing recipient still sends email; DB row is not duplicated."""
    monkeypatch.setattr(main, "APP_BASE_URL", "https://example.com")
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")

    sender = {"customer_id": "sender-1", "email": "sender@company.com"}
    assignment = {
        "customer_site_id": "cs-sender-1",
        "site_id": "site-1",
        "is_report_ready": True,
        "account_id": "acct-1",
    }
    existing_row = {
        "customer_site_id": "cs-recipient-1",
        "site_id": "site-1",
        "account_id": "acct-1",
        "assigned_via": "shared_site",
        "shared_by": "sender-1",
    }
    emails_sent: list[str] = []

    async def fake_get_authenticated_customer(db, request):
        return sender

    async def fake_resolve_sender_report_assignment(db, customer_id, *, customer_site_id=None, site_id=None):
        return assignment

    async def fake_find_customer_by_email(db, email):
        if email == "recipient@company.com":
            return {"customer_id": "recipient-1", "email": email}
        return None

    async def fake_ensure_shared_site_assignment(db, customer_id, site_id, shared_by_customer_id, **kwargs):
        assert customer_id == "recipient-1"
        assert site_id == "site-1"
        assert shared_by_customer_id == "sender-1"
        return existing_row

    async def fake_send_report_share_email(recipient, *args):
        emails_sent.append(recipient)

    class FakeDb:
        async def request(self, *args, **kwargs):
            if kwargs.get("params", {}).get("site_id") == "eq.site-1":
                return [{"site_id": "site-1", "company_name": "Acme", "full_address": "1 Main St"}]
            return []

    monkeypatch.setattr(main, "get_admin_db", lambda: FakeDb())
    monkeypatch.setattr(main, "get_authenticated_customer", fake_get_authenticated_customer)
    monkeypatch.setattr(main, "resolve_sender_report_assignment", fake_resolve_sender_report_assignment)
    monkeypatch.setattr(main, "find_customer_by_email", fake_find_customer_by_email)
    monkeypatch.setattr(main, "ensure_shared_site_assignment", fake_ensure_shared_site_assignment)
    monkeypatch.setattr(main, "send_report_share_email", fake_send_report_share_email)

    class FakeRequest:
        cookies = {}

    response = await main.share_report(
        FakeRequest(),
        {
            "customer_site_id": "cs-sender-1",
            "site_id": "site-1",
            "emails": ["recipient@company.com"],
        },
    )

    assert response["status"] == "complete"
    assert response["sent"] == 1
    assert response["failed"] == 0
    assert response["results"] == [{"email": "recipient@company.com", "status": "sent"}]
    assert emails_sent == ["recipient@company.com"]


@pytest.mark.asyncio
async def test_frontend_config_does_not_expose_share_secret(monkeypatch):
    """Test that the share secret is not exposed via the frontend config endpoint."""
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")

    config = await main.frontend_config()

    assert "SHARE_TOKEN_SECRET" not in config
    assert "share_token_secret" not in config
    assert "test-secret" not in str(config.values())
