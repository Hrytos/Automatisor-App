import pytest

import backend.chat as chat


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class FakeDb:
    async def request(self, method, path, params=None, json_body=None, headers=None):
        if path == "/rest/v1/automatisor_customer_sites":
            return [{"report_context_high": {}, "report_context_all": {}}]
        if path == "/rest/v1/automatisor_chatbot" and method == "GET":
            return [{"messages": []}]
        if path == "/rest/v1/automatisor_chatbot" and method == "PATCH":
            return None
        raise AssertionError(f"Unexpected DB call: {method} {path}")


def _fake_deps():
    async def fake_get_authenticated_customer(db, request):
        return {"customer_id": "cust-1"}

    def fake_infer_error_status(detail):
        return 500

    return FakeDb, fake_get_authenticated_customer, fake_infer_error_status


def test_is_disallowed_request_matches_email_drafting():
    assert chat._is_disallowed_request(
        "Frame an email to the warehouse manager pitching a robotic picking system"
    )
    assert not chat._is_disallowed_request(
        "Suggest top 3 automation solutions that make sense for this facility"
    )


def test_build_disallowed_request_response_is_intent_aware():
    reply = chat._build_disallowed_request_response(
        "Write an email pitching robotic picking for this site"
    )
    assert reply.startswith("I can only answer questions about this report.")
    assert "Try asking:" in reply
    assert "robotic picking" in reply.lower()
    assert "[topic]" not in reply


@pytest.mark.asyncio
async def test_send_message_short_circuits_disallowed_request(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    class ForbiddenOpenAI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("OpenAI should not be called for disallowed requests")

    monkeypatch.setattr(chat, "AsyncOpenAI", ForbiddenOpenAI)

    response = await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "Draft an email to the warehouse manager about robotic picking",
            }
        )
    )

    assert response["reply"].startswith("I can only answer questions about this report.")
    assert "Try asking:" in response["reply"]


@pytest.mark.asyncio
async def test_send_message_replaces_disallowed_model_output(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    class FakeCompletion:
        def __init__(self):
            self.choices = [
                type(
                    "Choice",
                    (),
                    {"message": type("Message", (), {"content": "Subject: Automation proposal\n\nHi there\n\n...\n\nBest regards,"})()},
                )
            ]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type(
                "ChatApi",
                (),
                {"completions": type("Completions", (), {"create": self.create})()},
            )

        async def create(self, *args, **kwargs):
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    response = await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "What are the top automation solutions for this site?",
            }
        )
    )

    assert response["reply"].startswith("I can only answer questions about this report.")
    assert "Try asking:" in response["reply"]