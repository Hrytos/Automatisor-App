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
    
    # Create old-style token without shared_by
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
async def test_frontend_config_does_not_expose_share_secret(monkeypatch):
    """Test that the share secret is not exposed via the frontend config endpoint."""
    monkeypatch.setattr(main, "SHARE_TOKEN_SECRET", "test-secret")

    config = await main.frontend_config()

    assert "SHARE_TOKEN_SECRET" not in config
    assert "share_token_secret" not in config
    assert "test-secret" not in str(config.values())
