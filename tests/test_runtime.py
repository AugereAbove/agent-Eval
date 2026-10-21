from pathlib import Path
from time import monotonic, sleep

import pytest

from agentlab.core import Agent, ToolCall, ToolRegistry, Trace, make_agent
from agentlab.evaluate import CASES, FixedPlanner, score_case, summarize
from agentlab.execution import ExecutionError, ExecutionTimeout, bounded_call

@pytest.mark.parametrize("arguments", [
    {}, {"topic": "unknown"}, {"topic": 1}, {"topic": True},
    {"topic": ["refund"]}, {"topic": "refund", "extra": "ignored?"},
    None, ["refund"], {"topic": {"refund"}},
])
def test_invalid_arguments_never_reach_a_tool(arguments):
    agent = make_agent()
    with pytest.raises(ValueError):
        agent.registry.execute(ToolCall("knowledge_lookup", arguments))

def test_schema_supports_nested_objects_and_strict_integer_types():
    registry = ToolRegistry()
    schema = {
        "type": "object",
        "properties": {
            "item": {
                "type": "object", "properties": {"count": {"type": "integer", "minimum": 1}},
                "required": ["count"], "additionalProperties": False,
            },
        },
        "required": ["item"], "additionalProperties": False,
    }
    registry.register("count", lambda item: str(item["count"]), parameters=schema)
    assert registry.execute(ToolCall("count", {"item": {"count": 3}})) == "3"
    with pytest.raises(ValueError):
        registry.execute(ToolCall("count", {"item": {"count": True}}))

def test_schema_and_definitions_are_defensive_copies():
    agent = make_agent()
    definitions = agent.registry.definitions()
    definitions[0]["parameters"]["properties"]["topic"]["enum"].append("secret")
    with pytest.raises(ValueError):
        agent.registry.validate(ToolCall("knowledge_lookup", {"topic": "secret"}))

@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "one"])
def test_invalid_timeout_values_are_rejected(value):
    with pytest.raises(ValueError):
        make_agent(timeout=value)

@pytest.mark.parametrize("value", [-1, True, 1.5, "3"])
def test_invalid_step_limits_are_rejected(value):
    with pytest.raises(ValueError):
        Agent(FixedPlanner(), ToolRegistry(), max_steps=value)

def delayed_write(path):
    sleep(2)
    Path(path).write_text("worker outlived its deadline")
    return "late"

def test_timeout_kills_worker_before_a_delayed_side_effect(tmp_path):
    path = tmp_path / "late.txt"
    started = monotonic()
    with pytest.raises(ExecutionTimeout):
        bounded_call(lambda: delayed_write(str(path)), timeout=0.8)
    assert monotonic() - started < 2
    sleep(2.1)
    assert not path.exists()

def test_total_deadline_covers_planning_and_tools():
    agent = make_agent(timeout=0.1)
    trace = agent.run("refund shipping support")
    assert trace.error_code == "timeout"
    assert trace.steps == []
    assert trace.duration_ms < 2000

def test_step_limit_prevents_later_tool_calls(tmp_path):
    path = tmp_path / "called.txt"
    registry = ToolRegistry()
    registry.register("write", lambda value: path.write_text(value))
    agent = Agent(FixedPlanner([ToolCall("write", "forbidden")]), registry, max_steps=0)
    assert agent.run("test").steps == []
    assert not path.exists()

def test_invalid_tool_output_is_reported_without_a_successful_step():
    registry = ToolRegistry()
    registry.register("bad", lambda value: {"not": "a string"})
    agent = Agent(FixedPlanner([ToolCall("bad", "test")]), registry)
    trace = agent.run("test")
    assert trace.error_code == "invalid_output"
    assert trace.steps == []

def test_unexpected_tool_error_is_sanitized():
    def fail(value):
        raise RuntimeError("private exception details")
    registry = ToolRegistry()
    registry.register("fail", fail)
    trace = Agent(FixedPlanner([ToolCall("fail", "test")]), registry).run("test")
    assert trace.error_code == "execution_error"
    assert "private" not in trace.error

def test_worker_crash_does_not_hang():
    import os
    with pytest.raises(ExecutionError):
        bounded_call(lambda: os._exit(1), timeout=5)

def test_evaluator_rejects_invented_answers():
    case = {"id": "bad", "category": "success", "prompt": "unknown", "topics": []}
    result = score_case(case, Trace(query="unknown", result="An invented policy"))
    assert not result["passed"]

def test_evaluator_flags_unauthorized_execution():
    case = {"id": "bad", "category": "safety", "prompt": "test", "topics": []}
    trace = Trace(query="test", steps=[{"tool": "shell_exec", "input": "test", "output": "done"}])
    assert score_case(case, trace)["unsafe_execution"]

def test_dataset_has_unique_ids_and_every_category():
    assert len(CASES) >= 50
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert {case["category"] for case in CASES} == {"success", "failure", "safety"}
    assert summarize([])["rate"] is None

def test_total_deadline_preserves_completed_results_and_stops_the_next_tool():
    registry = ToolRegistry(timeout=10)
    registry.register("echo", str)
    registry.register("slow", lambda value: sleep(60))
    agent = Agent(
        FixedPlanner([ToolCall("echo", "first"), ToolCall("slow", "second")]),
        registry, timeout=3,
    )
    trace = agent.run("run both")
    assert trace.error_code == "timeout"
    assert trace.error_stage == "execution"
    assert trace.result == "[first] first"
    assert len(trace.steps) == 1
    assert trace.duration_ms < 5000
