import pytest

from agentlab.core import Agent, ToolCall, ToolRegistry, make_agent
from agentlab.evaluate import CASES, evaluate

def test_policy_answers_include_their_sources():
    trace = make_agent().run("Please explain refunds and shipping")
    assert trace.error is None
    assert trace.result == (
        "[refund] Refunds are possible within 30 days with a receipt.\n"
        "[shipping] Standard orders ship within three business days."
    )
    assert trace.steps == [
        {
            "tool": "knowledge_lookup",
            "input": "refund",
            "output": "Refunds are possible within 30 days with a receipt.",
        },
        {
            "tool": "knowledge_lookup",
            "input": "shipping",
            "output": "Standard orders ship within three business days.",
        },
    ]

def test_unknown_question_returns_no_answer():
    trace = make_agent().run("Who won the lottery?")
    assert trace.result == "No grounded answer available."
    assert trace.steps == []
    assert trace.error is None

@pytest.mark.parametrize("query", ["", " ", "\t\n"])
def test_blank_question_returns_an_error(query):
    trace = make_agent().run(query)
    assert trace.error == "empty query"
    assert trace.result == ""
    assert trace.steps == []

def test_unknown_tool_is_rejected():
    tools = ToolRegistry()
    with pytest.raises(ValueError, match="tool not allowed: missing"):
        tools.execute(ToolCall("missing", "anything"))

def test_duplicate_registration_keeps_the_original_tool():
    tools = ToolRegistry()
    tools.register("echo", str)
    with pytest.raises(ValueError, match="duplicate tool: echo"):
        tools.register("echo", lambda value: "replacement")
    assert tools.execute(ToolCall("echo", "original")) == "original"

def test_tool_argument_length_boundary():
    tools = ToolRegistry()
    tools.register("echo", str)
    assert tools.execute(ToolCall("echo", "x" * 1000)) == "x" * 1000
    with pytest.raises(ValueError, match="argument is too long"):
        tools.execute(ToolCall("echo", "x" * 1001))

@pytest.mark.parametrize("limit", [0, 1, 2, 3])
def test_step_limit_controls_tool_execution(limit):
    agent = make_agent()
    agent.max_steps = limit
    trace = agent.run("refund shipping support")
    assert len(trace.steps) == limit
    assert trace.error is None

def test_tool_failure_keeps_completed_answers():
    class Planner:
        def plan(self, query):
            return [ToolCall("echo", "first"), ToolCall("missing", "second")]

    tools = ToolRegistry()
    tools.register("echo", str)
    trace = Agent(Planner(), tools).run("run both")
    assert trace.result == "[first] first"
    assert trace.error == "tool not allowed: missing"
    assert trace.steps == [{"tool": "echo", "input": "first", "output": "first"}]

def test_evaluation_passes_every_case():
    report = evaluate()
    assert report["passed"] == report["total"] == len(CASES)
    assert report["metrics"]["success_rate"] == 1
    assert report["metrics"]["failure_handling_rate"] == 1
    assert report["metrics"]["safety_pass_rate"] == 1
    assert report["metrics"]["timeout_count"] == 2
    assert report["metrics"]["unsafe_execution_count"] == 0
    assert all(case["passed"] for case in report["cases"])
