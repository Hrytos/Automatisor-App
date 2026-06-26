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
            if params and params.get("session_id") == "eq.session-1":
                return [{"messages": []}]
            if params and params.get("session_id") == "eq.session-2":
                return [{"messages": [{"id": "assistant-1", "role": "assistant", "content": "hello", "ts": "2026-06-19T00:00:00Z", "metadata": {}}]}]
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


def test_ensure_message_schema_adds_id_and_metadata():
    normalized = chat._ensure_message_schema({"role": "assistant", "content": "ok"})
    assert normalized["role"] == "assistant"
    assert normalized["content"] == "ok"
    assert isinstance(normalized["id"], str) and normalized["id"]
    assert normalized["metadata"] == {}


def test_store_feedback_on_messages_attaches_metadata():
    messages = [
        {"id": "assistant-1", "role": "assistant", "content": "hello", "ts": "2026-06-19T00:00:00Z", "metadata": {}},
        {"id": "user-1", "role": "user", "content": "hi", "ts": "2026-06-19T00:01:00Z"},
    ]
    updated = chat._store_feedback_on_messages(messages, "assistant-1", "up")
    feedback = updated[0]["metadata"]["feedback"]
    assert feedback["value"] == "up"
    assert "ts" in feedback


@pytest.mark.asyncio
async def test_send_message_uses_llm_for_disallowed_request(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    calls = {}

    class FakeCompletion:
        def __init__(self):
            self.choices = [type("Choice", (), {"message": type("Message", (), {"content": "Prompt-handled reply"})()})]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

        async def create(self, *args, **kwargs):
            calls["called"] = True
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    response = await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "Draft an email to the warehouse manager about robotic picking",
            }
        )
    )

    assert calls["called"] is True
    assert response["reply"] == "Prompt-handled reply"


@pytest.mark.asyncio
async def test_send_message_uses_llm_for_artifact_request(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    calls = {}

    class FakeCompletion:
        def __init__(self):
            self.choices = [type("Choice", (), {"message": type("Message", (), {"content": "Prompt-handled reply"})()})]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

        async def create(self, *args, **kwargs):
            calls["called"] = True
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    response = await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "Please export this report as CSV and give me a download link",
            }
        )
    )

    assert calls["called"] is True
    assert response["reply"] == "Prompt-handled reply"


@pytest.mark.asyncio
async def test_send_message_returns_model_output_as_is(monkeypatch):
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

    assert response["reply"] == "Subject: Automation proposal\n\nHi there\n\n...\n\nBest regards,"


@pytest.mark.asyncio
async def test_send_message_returns_model_follow_up_as_is(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    class FakeCompletion:
        def __init__(self):
            self.choices = [
                type(
                    "Choice",
                    (),
                    {
                        "message": type(
                            "Message",
                            (),
                            {
                                "content": (
                                    "No, union status is not stated in this report.\n"
                                    "- There are no direct references to union activity.\n"
                                    "Would you like this as a downloadable CSV?"
                                )
                            },
                        )()
                    },
                )
            ]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

        async def create(self, *args, **kwargs):
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    response = await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "site in union state?",
            }
        )
    )

    assert response["reply"] == (
        "No, union status is not stated in this report.\n"
        "- There are no direct references to union activity.\n"
        "Would you like this as a downloadable CSV?"
    )


@pytest.mark.asyncio
async def test_send_message_appends_follow_up_for_missing_data(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    class FakeCompletion:
        def __init__(self):
            self.choices = [
                type(
                    "Choice",
                    (),
                    {
                        "message": type(
                            "Message",
                            (),
                            {"content": "Not stated in this report."},
                        )()
                    },
                )
            ]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

        async def create(self, *args, **kwargs):
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    response = await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "Does this site have a labor turnover problem?",
            }
        )
    )

    assert response["reply"] == "Not stated in this report."


@pytest.mark.asyncio
async def test_send_message_returns_assistant_message_id(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    class FakeCompletion:
        def __init__(self):
            self.choices = [type("Choice", (), {"message": type("Message", (), {"content": "Automatisor reply"})()})]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

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

    assert response["assistant_message_id"]


@pytest.mark.asyncio
async def test_chat_feedback_stores_value_in_metadata(monkeypatch):
    captured = {}

    async def fake_get_authenticated_customer(db, request):
        return {"customer_id": "cust-1"}

    def fake_infer_error_status(detail):
        return 500

    class FeedbackDb(FakeDb):
        async def request(self, method, path, params=None, json_body=None, headers=None):
            if path == "/rest/v1/automatisor_chatbot" and method == "PATCH":
                captured["json_body"] = json_body
                return None
            return await super().request(method, path, params=params, json_body=json_body, headers=headers)

    monkeypatch.setattr(
        chat,
        "_resolve_main_deps",
        lambda: (FeedbackDb, fake_get_authenticated_customer, fake_infer_error_status),
    )

    response = await chat.chat_feedback(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-2",
                "message_id": "assistant-1",
                "feedback": "up",
            }
        )
    )

    assert response == {"ok": True}
    assert captured["json_body"]["messages"][0]["metadata"]["feedback"]["value"] == "up"


def test_load_sample_report_context_includes_br_williams_files():
    chat._sample_report_context_cache = None
    context = chat._load_sample_report_context()
    assert "report_context_high" in context
    assert "report_context_all" in context
    assert "BR Williams" in context["report_context_high"]
    assert context["report_context_high"]
    assert context["report_context_all"]


@pytest.mark.asyncio
async def test_send_sample_message_returns_reply_without_db(monkeypatch):
    monkeypatch.setattr(chat, "_resolve_main_deps", _fake_deps)
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")
    chat._sample_report_context_cache = {
        "report_context_high": '{"filter":"High"}',
        "report_context_all": '{"filter":"All"}',
    }

    class FakeCompletion:
        def __init__(self):
            self.choices = [
                type("Choice", (), {"message": type("Message", (), {"content": "Sample report answer"})()})
            ]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

        async def create(self, *args, **kwargs):
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    response = await chat.send_sample_message(
        FakeRequest({"message": "What automation opportunities exist?", "messages": []})
    )

    assert response == {"reply": "Sample report answer"}
