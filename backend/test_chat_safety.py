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
        if path == "/rest/v1/automatisor_source_sites":
            return []
        if path == "/rest/v1/automatisor_customer_context":
            return []
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


def test_has_report_context_detects_nonempty_payload():
    assert chat._has_report_context({"report_context_high": {"a": 1}}) is True
    assert chat._has_report_context({"report_context_high": {}, "report_context_all": {}}) is False
    assert chat._has_report_context(None) is False


def test_metadata_has_content_skips_empty_values():
    assert chat._metadata_has_content(None) is False
    assert chat._metadata_has_content({}) is False
    assert chat._metadata_has_content([]) is False
    assert chat._metadata_has_content({"permits": []}) is False
    assert chat._metadata_has_content({"establishment_id": "123"}) is True


def test_build_source_site_context_from_rows_filters_types_and_empty_metadata():
    rows = [
        {"source_type": "OSHA Establishment", "metadata": {"establishment_id": "1"}},
        {"source_type": "Building Permit", "metadata": {}},
        {"source_type": "Google Reviews", "metadata": {"rating": 4.2}},
        {"source_type": "Image Analysis", "metadata": {"dock_count": 12}},
    ]
    payload = chat._build_source_site_context_from_rows(rows)
    assert set(payload.keys()) == {"OSHA Establishment", "Image Analysis"}
    assert '"establishment_id":"1"' in payload["OSHA Establishment"]


def test_format_source_site_context_prompt_section_omits_when_empty():
    assert chat._format_source_site_context_prompt_section({}) == ""


def test_format_source_site_context_prompt_section_includes_present_types():
    section = chat._format_source_site_context_prompt_section(
        {"OSHA Accidents": '{"incidents":1}'}
    )
    assert "## SUPPORTING SOURCE EVIDENCE" in section
    assert "### OSHA Accidents" in section
    assert "do not recite as a separate section" in section.lower()
    assert "prefer the report" in section.lower()


def test_street_city_from_full_address_uses_first_two_parts():
    assert chat._street_city_from_full_address("123 Main St, Dallas, TX 75201") == "123 Main St, Dallas"
    assert chat._street_city_from_full_address("Warehouse Rd") == "Warehouse Rd"
    assert chat._street_city_from_full_address("") == ""


def test_format_session_display_label_for_facility_and_site():
    facility_label = chat._format_session_display_label(
        {"title": "Ops review", "chat_type": chat.CHAT_TYPE_FACILITY},
        None,
    )
    assert facility_label == "Ops review - facility"

    site_label = chat._format_session_display_label(
        {"title": "Picking lanes", "chat_type": chat.CHAT_TYPE_SITE},
        {"company_name": "Acme", "full_address": "10 Industrial Blvd, Austin, TX"},
    )
    assert site_label == "Picking lanes - Acme(10 Industrial Blvd, Austin)"


def test_collect_ready_facility_reports_only_includes_ready_with_context():
    sites_by_id = {
        "site-1": {
            "site_id": "site-1",
            "company_name": "Alpha",
            "full_address": "1 Main St",
        },
        "site-2": {
            "site_id": "site-2",
            "company_name": "Beta",
            "full_address": "2 Main St",
        },
    }
    assignments = [
        {
            "site_id": "site-1",
            "assigned_via": "user_added_site",
            "is_report_ready": True,
            "report_context_high": {"headline": "ready"},
            "report_context_all": {},
        },
        {
            "site_id": "site-2",
            "assigned_via": "shared_site",
            "is_report_ready": True,
            "report_context_high": {},
            "report_context_all": {},
        },
    ]
    reports = chat._collect_ready_facility_reports(sites_by_id, assignments)
    assert len(reports) == 1
    assert reports[0]["site_id"] == "site-1"


@pytest.mark.asyncio
async def test_send_message_includes_source_context_in_system_prompt(monkeypatch):
    captured: dict = {}

    class SourceDb(FakeDb):
        async def request(self, method, path, params=None, json_body=None, headers=None):
            if path == "/rest/v1/automatisor_source_sites":
                return [
                    {
                        "source_type": "Building Permit",
                        "metadata": {"permit_number": "BP-100"},
                    }
                ]
            return await super().request(method, path, params=params, json_body=json_body, headers=headers)

    async def fake_get_authenticated_customer(db, request):
        return {"customer_id": "cust-1"}

    def fake_infer_error_status(detail):
        return 500

    monkeypatch.setattr(
        chat,
        "_resolve_main_deps",
        lambda: (SourceDb, fake_get_authenticated_customer, fake_infer_error_status),
    )
    monkeypatch.setattr(chat, "_get_openai_key", lambda: "test-key")

    class FakeCompletion:
        def __init__(self):
            self.choices = [type("Choice", (), {"message": type("Message", (), {"content": "ok"})()})]

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = type("ChatApi", (), {})()
            self.chat.completions = type("Completions", (), {"create": self.create})()

        async def create(self, *args, **kwargs):
            captured["messages"] = kwargs.get("messages") or args[0]
            return FakeCompletion()

    monkeypatch.setattr(chat, "AsyncOpenAI", FakeOpenAI)

    await chat.send_message(
        FakeRequest(
            {
                "site_id": "site-1",
                "session_id": "session-1",
                "message": "Any recent building permits?",
            }
        )
    )

    system_prompt = captured["messages"][0]["content"]
    assert "### Building Permit" in system_prompt
    assert "BP-100" in system_prompt


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


@pytest.mark.asyncio
async def test_ensure_sample_site_row_creates_when_absent():
    import backend.main as main

    main._sample_site_data_cache = {
        "report_context_high": {"filter": "High"},
        "report_context_all": {"filter": "All"},
        "report_metadata": {"sections": []},
    }
    captured: dict = {}

    class SampleDb:
        async def request(self, method, path, params=None, json_body=None, headers=None):
            if method == "GET":
                return []
            captured["method"] = method
            captured["json_body"] = json_body
            return [{"customer_site_id": "new-csite-1", "site_id": main.SAMPLE_SITE_ID}]

    row = await main.ensure_sample_site_row(SampleDb(), "cust-1")
    assert row["customer_site_id"] == "new-csite-1"
    assert captured["json_body"]["assigned_via"] == "sample_site"
    assert captured["json_body"]["is_report_ready"] is True
    assert captured["json_body"]["site_id"] == main.SAMPLE_SITE_ID


@pytest.mark.asyncio
async def test_ensure_sample_site_row_returns_existing():
    import backend.main as main

    class SampleDb:
        async def request(self, method, path, params=None, json_body=None, headers=None):
            return [{"customer_site_id": "existing-csite-1", "site_id": main.SAMPLE_SITE_ID}]

    row = await main.ensure_sample_site_row(SampleDb(), "cust-1")
    assert row["customer_site_id"] == "existing-csite-1"
