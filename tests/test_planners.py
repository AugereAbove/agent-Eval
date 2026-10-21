import io
import json
from urllib.error import HTTPError

import pytest

from agentlab.core import ToolCall, make_agent
from agentlab.planners import LLMPlanner, PlannerError, request_openai

def response(*calls):
    return {"status": "completed", "output": [
        {"type": "function_call", "name": name, "arguments": arguments}
        for name, arguments in calls
    ]}

def test_real_planner_builds_strict_function_tools_and_parses_calls():
    agent = make_agent()
    captured = {}
    def transport(payload, timeout):
        captured.update(payload)
        assert timeout == 10
        return response(("knowledge_lookup", '{"topic":"refund"}'))
    planner = LLMPlanner("test-model", agent.registry, transport=transport)
    assert planner.plan("money back policy") == [ToolCall("knowledge_lookup", {"topic": "refund"})]
    assert captured["model"] == "test-model"
    assert captured["store"] is False
    assert captured["tools"][0]["strict"] is True
    assert captured["tools"][0]["parameters"]["additionalProperties"] is False

def test_mocked_llm_runs_through_the_agent_without_network_or_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    agent = make_agent()
    agent.planner = LLMPlanner("test-model", agent.registry, transport=lambda payload, timeout: response(
        ("knowledge_lookup", '{"topic":"refund"}'),
        ("knowledge_lookup", '{"topic":"support"}'),
    ))
    trace = agent.run("Can I get my money back and when can I reach someone?")
    assert trace.error is None
    assert len(trace.steps) == 2
    assert "30 days" in trace.result
    assert "Monday" in trace.result

@pytest.mark.parametrize("arguments", [
    'not json', '[]', 'null', '{"topic":"refund","topic":"support"}',
    '{"topic":"refund","path":"private"}', '{"topic":true}', '{"topic":"secrets"}', '{}',
])
def test_model_arguments_are_validated_before_execution(arguments):
    agent = make_agent()
    planner = LLMPlanner("test-model", agent.registry, transport=lambda payload, timeout: response(
        ("knowledge_lookup", arguments),
    ))
    with pytest.raises(ValueError):
        planner.plan("test")

def test_model_cannot_request_an_unregistered_tool():
    planner = LLMPlanner("test-model", make_agent().registry, transport=lambda payload, timeout: response(
        ("shell_exec", '{"command":"test"}'),
    ))
    with pytest.raises(ValueError, match="tool not allowed"):
        planner.plan("test")

@pytest.mark.parametrize("body", [
    {}, {"status": "incomplete", "output": []}, {"status": "completed", "output": None},
    {"status": "completed", "output": [None]},
    {"status": "completed", "output": [{"type": "message", "content": None}]},
    {"status": "completed", "output": [{"type": "function_call", "status": "in_progress"}]},
    {"status": "completed", "output": [{"type": "web_search_call"}]},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
])
def test_invalid_or_refused_provider_responses_fail_closed(body):
    planner = LLMPlanner("test-model", make_agent().registry, transport=lambda payload, timeout: body)
    with pytest.raises(PlannerError):
        planner.plan("test")

def test_free_text_is_never_used_as_a_grounded_answer():
    planner = LLMPlanner("test-model", make_agent().registry, transport=lambda payload, timeout: {
        "status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Refunds are unlimited"},
        ]}],
    })
    assert planner.plan("refund") == []

def test_no_credentials_are_required_until_a_live_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    planner = LLMPlanner("test-model", make_agent().registry)
    with pytest.raises(PlannerError, match="not configured"):
        planner.plan("refund")

def test_http_transport_uses_responses_api_without_exposing_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    def fake_open(request, timeout):
        assert request.full_url == "https://api.openai.com/v1/responses"
        assert request.get_header("Authorization") == "Bearer unit-test-placeholder"
        assert json.loads(request.data) == {"model": "test-model"}
        assert timeout == 2
        return io.BytesIO(b'{"status":"completed","output":[]}')
    monkeypatch.setattr("agentlab.planners.urlopen", fake_open)
    assert request_openai({"model": "test-model"}, 2)["status"] == "completed"

def test_http_errors_hide_provider_bodies_and_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    def fail(request, timeout):
        raise HTTPError(request.full_url, 401, "sensitive provider body", {}, None)
    monkeypatch.setattr("agentlab.planners.urlopen", fail)
    with pytest.raises(PlannerError, match="provider returned HTTP 401") as error:
        request_openai({}, 1)
    assert "sensitive" not in str(error.value)

@pytest.mark.parametrize("model", [None, "", " "])
def test_model_must_be_explicit(model):
    with pytest.raises(ValueError):
        LLMPlanner(model, make_agent().registry)
