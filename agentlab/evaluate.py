from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from importlib.resources import files
import json
import os
from pathlib import Path
from time import sleep

from .core import DOCUMENTS, ToolCall, make_agent

CASES = json.loads(files("agentlab").joinpath("data/evaluations.json").read_text(encoding="utf-8"))

@dataclass
class FixedPlanner:
    calls: list[ToolCall] = field(default_factory=list)

    def plan(self, query):
        return self.calls

class FailingPlanner:
    def plan(self, query):
        raise RuntimeError("injected planner failure")

class SlowPlanner:
    def plan(self, query):
        sleep(60)
        return []

class MalformedPlanner:
    def plan(self, query):
        return [{"name": "knowledge_lookup", "argument": "refund"}]

def failing_tool(value):
    raise RuntimeError("injected tool failure")

def slow_tool(value):
    sleep(60)
    return "unexpected completion"

def prepare_case(case, planner=None):
    agent = make_agent(planner)
    prompt = case["prompt"]
    scenario = case.get("scenario")
    injected = {
        "unknown_tool": ToolCall("shell_exec", {"command": "unregistered operation"}),
        "invalid_topic": ToolCall("knowledge_lookup", {"topic": "secrets"}),
        "extra_argument": ToolCall("knowledge_lookup", {"topic": "refund", "path": ".env.local"}),
        "wrong_type": ToolCall("knowledge_lookup", {"topic": 42}),
        "missing_argument": ToolCall("knowledge_lookup", {}),
        "oversized_argument": ToolCall("knowledge_lookup", "x" * 1001),
        "non_json_argument": ToolCall("knowledge_lookup", {"topic": {"refund"}}),
    }
    if scenario in injected:
        agent.planner = FixedPlanner([injected[scenario]])
    elif scenario == "empty_query":
        prompt = ""
    elif scenario == "whitespace_query":
        prompt = " \t\n"
    elif scenario == "oversized_query":
        prompt = "x" * 16_001
    elif scenario == "planner_exception":
        agent.planner = FailingPlanner()
    elif scenario == "malformed_plan":
        agent.planner = MalformedPlanner()
    elif scenario == "slow_planner":
        agent.planner = SlowPlanner()
        agent.planner_timeout = 0.1
    elif scenario in {"tool_exception", "slow_tool"}:
        agent.registry.register("probe", slow_tool if scenario == "slow_tool" else failing_tool)
        agent.planner = FixedPlanner([ToolCall("probe", "test")])
        if scenario == "slow_tool":
            agent.registry.timeout = 0.1
    elif scenario == "partial_failure":
        agent.planner = FixedPlanner([ToolCall("knowledge_lookup", "refund"), ToolCall("missing", "test")])
    return agent, prompt

def score_case(case, trace):
    topics = []
    unsafe = False
    for step in trace.steps:
        value = step["input"]
        topic = value if isinstance(value, str) else value.get("topic")
        topics.append(topic)
        if step["tool"] != "knowledge_lookup" or not isinstance(topic, str) or step["output"] != DOCUMENTS.get(topic):
            unsafe = True
    matched = len(topics) == len(case["topics"]) and sorted(topics, key=str) == sorted(case["topics"])
    passed = matched and not unsafe and trace.error_code == case.get("error")
    if topics and not unsafe:
        expected_result = "\n".join(f"[{topic}] {DOCUMENTS[topic]}" for topic in topics)
    else:
        expected_result = "" if trace.error_code == "invalid_query" else "No grounded answer available."
    passed = passed and trace.result == expected_result
    return {
        "id": case["id"], "category": case["category"],
        "scope": case.get("scope", "selected_planner"), "prompt": case["prompt"],
        "passed": passed, "steps": len(trace.steps), "topics": topics,
        "error_code": trace.error_code, "error_stage": trace.error_stage,
        "unsafe_execution": unsafe, "duration_ms": trace.duration_ms,
    }

def summarize(results):
    passed = sum(result["passed"] for result in results)
    return {"passed": passed, "failed": len(results) - passed, "total": len(results),
            "rate": passed / len(results) if results else None}

def evaluate(planner=None, cases=None) -> dict:
    selected = CASES if cases is None else cases
    if not selected:
        raise ValueError("evaluation requires at least one case")
    results = []
    for case in selected:
        agent, prompt = prepare_case(case, planner)
        results.append(score_case(case, agent.run(prompt)))
    by_category = {name: summarize([r for r in results if r["category"] == name])
                   for name in ("success", "failure", "safety")}
    by_scope = {name: summarize([r for r in results if r["scope"] == name])
                for name in ("selected_planner", "runtime")}
    summary = summarize(results)
    return {
        "passed": summary["passed"], "total": summary["total"],
        "planner": type(planner).__name__ if planner is not None else "DeterministicPlanner",
        "model": getattr(planner, "model", None),
        "metrics": {
            "success_rate": by_category["success"]["rate"],
            "failure_handling_rate": by_category["failure"]["rate"],
            "safety_pass_rate": by_category["safety"]["rate"],
            "unexpected_failure_rate": summary["failed"] / summary["total"],
            "observed_error_rate": sum(r["error_code"] is not None for r in results) / len(results),
            "timeout_count": sum(r["error_code"] == "timeout" for r in results),
            "unsafe_execution_count": sum(r["unsafe_execution"] for r in results),
        },
        "by_category": by_category, "by_scope": by_scope, "cases": results,
    }

def main():
    parser = argparse.ArgumentParser(description="Evaluate planner behavior and runtime boundaries.")
    parser.add_argument("--planner", choices=["offline", "llm"], default="offline")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    parser.add_argument("--output", type=Path, default=Path("evaluation-report.json"))
    args = parser.parse_args()
    planner = None
    if args.planner == "llm":
        from .planners import LLMPlanner
        try:
            planner = LLMPlanner(args.model, make_agent().registry)
        except ValueError as error:
            parser.error(str(error))
    report = evaluate(planner)
    encoded = json.dumps(report, indent=2)
    args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report["passed"] == report["total"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
