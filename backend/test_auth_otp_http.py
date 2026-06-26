"""Supabase Auth OTP HTTP client tests."""

import httpx
import pytest
from fastapi import HTTPException

import backend.main as main


class FakeAsyncClient:
    def __init__(self, *, response=None, exception=None, calls=None, **kwargs):
        self.response = response
        self.exception = exception
        self.calls = calls if calls is not None else []
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exception:
            raise self.exception
        return self.response


@pytest.fixture
def supabase_auth_env(monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_URL", "https://project-ref.supabase.co")
    monkeypatch.setattr(main, "SUPABASE_ANON_KEY", "anon-key")


@pytest.mark.asyncio
async def test_send_supabase_otp_posts_to_auth_endpoint(supabase_auth_env, monkeypatch):
    calls = []
    response = httpx.Response(200, json={})

    def fake_client(**kwargs):
        return FakeAsyncClient(response=response, calls=calls, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    await main.send_supabase_otp("person@company.com")

    assert calls == [
        (
            "https://project-ref.supabase.co/auth/v1/otp",
            {
                "headers": {
                    "apikey": "anon-key",
                    "Authorization": "Bearer anon-key",
                    "Content-Type": "application/json",
                },
                "json": {"email": "person@company.com", "create_user": True},
            },
        )
    ]


@pytest.mark.asyncio
async def test_send_supabase_otp_maps_timeout(supabase_auth_env, monkeypatch):
    def fake_client(**kwargs):
        return FakeAsyncClient(exception=httpx.ReadTimeout("read operation timed out"), **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    with pytest.raises(HTTPException) as exc:
        await main.send_supabase_otp("person@company.com")

    assert exc.value.status_code == 504
    assert exc.value.detail == "We could not confirm the OTP was sent. Please try again."


@pytest.mark.asyncio
async def test_send_supabase_otp_maps_rate_limit(supabase_auth_env, monkeypatch):
    response = httpx.Response(
        429,
        json={"msg": "For security purposes, you can only request this after 15 seconds"},
    )

    def fake_client(**kwargs):
        return FakeAsyncClient(response=response, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    with pytest.raises(HTTPException) as exc:
        await main.send_supabase_otp("person@company.com")

    assert exc.value.status_code == 429
    assert exc.value.detail == "For security purposes, you can only request this after 15 seconds"


@pytest.mark.asyncio
async def test_verify_supabase_otp_returns_session_payload(supabase_auth_env, monkeypatch):
    response = httpx.Response(
        200,
        json={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "token_type": "bearer",
            "user": {"id": "user-1", "email": "person@company.com"},
        },
    )

    def fake_client(**kwargs):
        return FakeAsyncClient(response=response, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    payload = await main.verify_supabase_otp("person@company.com", "123456")

    assert payload == {
        "session": {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
            "token_type": "bearer",
        },
        "user": {"id": "user-1"},
    }


@pytest.mark.asyncio
async def test_verify_supabase_otp_maps_supabase_error(supabase_auth_env, monkeypatch):
    response = httpx.Response(422, json={"msg": "Token has expired or is invalid"})

    def fake_client(**kwargs):
        return FakeAsyncClient(response=response, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    with pytest.raises(HTTPException) as exc:
        await main.verify_supabase_otp("person@company.com", "123456")

    assert exc.value.status_code == 422
    assert exc.value.detail == "Token has expired or is invalid"
