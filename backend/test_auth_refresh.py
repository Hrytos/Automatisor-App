"""Auth refresh token and cookie tests."""

import httpx
import pytest
from fastapi import HTTPException
from starlette.responses import Response

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
    monkeypatch.setattr(main, "COOKIE_SECURE", False)


def test_set_auth_cookies_sets_access_and_refresh():
    response = Response()
    main.set_auth_cookies(
        response,
        {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        },
    )
    set_cookie_headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
    assert any("access_token=access-token" in header for header in set_cookie_headers)
    assert any("refresh_token=refresh-token" in header for header in set_cookie_headers)


def test_clear_auth_cookies_deletes_both_tokens():
    response = Response()
    main.clear_auth_cookies(response)
    set_cookie_headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
    assert any("access_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("refresh_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)


@pytest.mark.asyncio
async def test_refresh_supabase_session_posts_to_token_endpoint(supabase_auth_env, monkeypatch):
    calls = []
    response = httpx.Response(
        200,
        json={
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "bearer",
            "user": {"id": "user-1", "email": "person@company.com"},
        },
    )

    def fake_client(**kwargs):
        return FakeAsyncClient(response=response, calls=calls, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    payload = await main.refresh_supabase_session("old-refresh-token")

    assert payload["session"]["access_token"] == "new-access-token"
    assert payload["session"]["refresh_token"] == "new-refresh-token"
    assert calls == [
        (
            "https://project-ref.supabase.co/auth/v1/token?grant_type=refresh_token",
            {
                "headers": {
                    "apikey": "anon-key",
                    "Authorization": "Bearer anon-key",
                    "Content-Type": "application/json",
                },
                "json": {"refresh_token": "old-refresh-token"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_refresh_supabase_session_maps_invalid_refresh_to_401(supabase_auth_env, monkeypatch):
    response = httpx.Response(401, json={"msg": "Invalid Refresh Token"})

    def fake_client(**kwargs):
        return FakeAsyncClient(response=response, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)

    with pytest.raises(HTTPException) as exc:
        await main.refresh_supabase_session("bad-refresh-token")

    assert exc.value.status_code == 401
    assert exc.value.detail == "Session expired. Please sign in again."


@pytest.mark.asyncio
async def test_refresh_session_endpoint_sets_new_cookies(supabase_auth_env, monkeypatch):
    async def fake_refresh_supabase_session(refresh_token):
        assert refresh_token == "stored-refresh-token"
        return {
            "session": {
                "access_token": "new-access-token",
                "refresh_token": "rotated-refresh-token",
            }
        }

    monkeypatch.setattr(main, "refresh_supabase_session", fake_refresh_supabase_session)

    class FakeRequest:
        cookies = {"refresh_token": "stored-refresh-token"}

    response = Response()
    payload = await main.refresh_session(response, FakeRequest())

    assert payload == {"status": "refreshed"}
    set_cookie_headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
    assert any("access_token=new-access-token" in header for header in set_cookie_headers)
    assert any("refresh_token=rotated-refresh-token" in header for header in set_cookie_headers)


@pytest.mark.asyncio
async def test_refresh_session_endpoint_requires_refresh_cookie():
    class FakeRequest:
        cookies = {}

    response = Response()
    with pytest.raises(HTTPException) as exc:
        await main.refresh_session(response, FakeRequest())

    assert exc.value.status_code == 401
    assert exc.value.detail == "No active session."


@pytest.mark.asyncio
async def test_auth_logout_clears_both_cookies():
    response = Response()
    payload = await main.auth_logout(response)
    assert payload == {"status": "logged_out"}
    set_cookie_headers = [value.decode() for key, value in response.raw_headers if key == b"set-cookie"]
    assert any("access_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("refresh_token=" in header and "Max-Age=0" in header for header in set_cookie_headers)
