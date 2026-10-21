import argparse
from dataclasses import asdict
import json
import os

from .core import make_agent

def main():
    parser = argparse.ArgumentParser(description="Run a bounded policy lookup agent.")
    parser.add_argument("question", nargs="*", default=[])
    parser.add_argument("--planner", choices=["offline", "llm"], default="offline")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL"))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--planner-timeout", type=float, default=10)
    parser.add_argument("--tool-timeout", type=float, default=5)
    args = parser.parse_args()
    try:
        agent = make_agent(timeout=args.timeout, planner_timeout=args.planner_timeout, tool_timeout=args.tool_timeout)
        if args.planner == "llm":
            from .planners import LLMPlanner
            agent.planner = LLMPlanner(args.model, agent.registry, timeout=args.planner_timeout)
    except ValueError as error:
        parser.error(str(error))
    trace = agent.run(" ".join(args.question) or "What is the refund policy?")
    output = asdict(trace)
    output["question"] = output.pop("query")
    print(json.dumps(output, indent=2))
    return 1 if trace.error else 0

if __name__ == "__main__":
    raise SystemExit(main())
