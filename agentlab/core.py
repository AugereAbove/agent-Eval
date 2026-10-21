from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

DOCUMENTS = {
    "refund": "Refunds are possible within 30 days with a receipt.",
    "shipping": "Standard orders ship within three business days.",
    "support": "Support is available Monday through Friday.",
}

@dataclass(frozen=True)
class ToolCall:
    name: str
    argument: str

@dataclass
class Trace:
    query: str
    steps: list[dict[str, str]] = field(default_factory=list)
    result: str = ""
    error: str | None = None

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[[str], str]] = {}

    def register(self, name: str, tool: Callable[[str], str]) -> None:
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        self._tools[name] = tool

    def execute(self, call: ToolCall) -> str:
        if call.name not in self._tools:
            raise ValueError(f"tool not allowed: {call.name}")
        if len(call.argument) > 1000:
            raise ValueError("argument is too long")
        tool = self._tools[call.name]
        return tool(call.argument)

class DeterministicPlanner:
    def plan(self, query: str) -> list[ToolCall]:
        question = query.lower()
        matches = [topic for topic in DOCUMENTS if topic in question]
        return [ToolCall("knowledge_lookup", topic) for topic in matches[:3]]

class Agent:
    def __init__(
        self,
        planner: DeterministicPlanner,
        registry: ToolRegistry,
        max_steps: int = 3,
    ):
        self.planner = planner
        self.registry = registry
        self.max_steps = max_steps

    def run(self, query: str) -> Trace:
        trace = Trace(query=query)
        if not query.strip():
            trace.error = "empty query"
            return trace

        calls = self.planner.plan(query)
        answers: list[str] = []
        for call in calls[:self.max_steps]:
            try:
                answer = self.registry.execute(call)
                trace.steps.append({
                    "tool": call.name,
                    "input": call.argument,
                    "output": answer,
                })
                answers.append(f"[{call.argument}] {answer}")
            except ValueError as error:
                trace.error = str(error)
                break

        trace.result = "\n".join(answers) or "No grounded answer available."
        return trace

def make_agent() -> Agent:
    tools = ToolRegistry()
    tools.register("knowledge_lookup", lambda topic: DOCUMENTS.get(topic, "No document found"))
    return Agent(DeterministicPlanner(), tools)
