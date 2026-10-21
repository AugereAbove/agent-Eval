from __future__ import annotations

import json
from pathlib import Path

from .core import make_agent

CASES = [
    ("What is the refund policy?", "[refund]", 1),
    ("When does shipping happen?", "[shipping]", 1),
    ("Tell me about support and refunds", "[refund]", 2),
    ("What's the office paint color?", "No grounded", 0),
]

def evaluate() -> dict:
    agent = make_agent()
    results = []
    for question, expected, minimum_steps in CASES:
        trace = agent.run(question)
        steps = len(trace.steps)
        passed = (
            expected in trace.result
            and steps >= minimum_steps
            and trace.error is None
        )
        results.append({"prompt": question, "passed": passed, "steps": steps})

    return {
        "passed": sum(case["passed"] for case in results),
        "total": len(results),
        "cases": results,
    }

if __name__ == "__main__":
    report = json.dumps(evaluate(), indent=2)
    Path("evaluation-report.json").write_text(report + "\n")
    print(report)
