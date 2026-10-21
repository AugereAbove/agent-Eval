from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from time import monotonic
from typing import Callable, Protocol

from jsonschema import Draft202012Validator

from .execution import ExecutionError, ExecutionTimeout, bounded_call, check_timeout

DOCUMENTS = {
    "refund": "Refunds are possible within 30 days with a receipt.",
    "shipping": "Standard orders ship within three business days.",
    "support": "Support is available Monday through Friday.",
}

class ToolError(ValueError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code

@dataclass(frozen=True)
class ToolCall:
    name: str
    argument: str | dict

@dataclass
class Trace:
    query: str
    steps: list[dict] = field(default_factory=list)
    result: str = ""
    error: str | None = None
    error_code: str | None = None
    error_stage: str | None = None
    duration_ms: float = 0

class Planner(Protocol):
    def plan(self, query: str) -> list[ToolCall]: ...

@dataclass
class RegisteredTool:
    function: Callable
    parameters: dict
    description: str
    legacy: bool

class ToolRegistry:
    def __init__(self, timeout: float = 5):
        self.timeout = check_timeout(timeout)
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, tool: Callable, parameters: dict | None = None, description: str = ""):
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        legacy = parameters is None
        if parameters is None:
            parameters = {
                "type": "object",
                "properties": {"value": {"type": "string", "maxLength": 1000}},
                "required": ["value"],
                "additionalProperties": False,
            }
        Draft202012Validator.check_schema(parameters)
        if parameters.get("type") != "object":
            raise ValueError("tool parameters must describe an object")
        self._tools[name] = RegisteredTool(tool, deepcopy(parameters), description, legacy)

    def definitions(self) -> list[dict]:
        return [
            {
                "type": "function",
                "name": name,
                "description": tool.description,
                "parameters": deepcopy(tool.parameters),
                "strict": True,
            }
            for name, tool in self._tools.items()
        ]

    def validate(self, call: ToolCall) -> dict:
        if not isinstance(call, ToolCall) or not isinstance(call.name, str):
            raise ToolError("invalid tool call", "invalid_arguments")
        if call.name not in self._tools:
            raise ToolError(f"tool not allowed: {call.name}", "tool_not_allowed")
        tool = self._tools[call.name]
        arguments = call.argument
        if isinstance(arguments, str):
            if len(arguments) > 1000:
                raise ToolError("argument is too long", "invalid_arguments")
            properties = tool.parameters.get("properties", {})
            if len(properties) == 1:
                arguments = {next(iter(properties)): arguments}
        try:
            encoded = json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ToolError("arguments must be JSON values", "invalid_arguments") from error
        if len(encoded) > 16_000:
            raise ToolError("arguments are too large", "invalid_arguments")
        if not Draft202012Validator(tool.parameters).is_valid(arguments):
            raise ToolError("tool arguments do not match the schema", "invalid_arguments")
        return arguments

    def execute(self, call: ToolCall, timeout: float | None = None) -> str:
        arguments = self.validate(call)
        tool = self._tools[call.name]
        budget = min(self.timeout, check_timeout(timeout)) if timeout is not None else self.timeout

        def invoke():
            result = tool.function(arguments["value"]) if tool.legacy else tool.function(**arguments)
            if not isinstance(result, str) or len(result) > 16_000:
                raise ToolError("tool must return a string of at most 16000 characters", "invalid_output")
            return result

        return bounded_call(invoke, budget)

class DeterministicPlanner:
    def plan(self, query: str) -> list[ToolCall]:
        question = query.lower()
        return [ToolCall("knowledge_lookup", topic) for topic in DOCUMENTS if topic in question][:3]

class Agent:
    def __init__(self, planner: Planner, registry: ToolRegistry, max_steps: int = 3,
                 timeout: float = 30, planner_timeout: float = 10):
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 0:
            raise ValueError("max_steps must be a nonnegative integer")
        self.planner = planner
        self.registry = registry
        self.max_steps = max_steps
        self.timeout = check_timeout(timeout)
        self.planner_timeout = check_timeout(planner_timeout)

    def run(self, query: str) -> Trace:
        started = monotonic()
        trace = Trace(query=query)
        deadline = started + self.timeout
        answers = []
        stage = "planning"
        try:
            if not isinstance(query, str) or not query.strip():
                raise ToolError("empty query", "invalid_query")
            if len(query) > 16_000:
                raise ToolError("query is too long", "invalid_query")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ExecutionTimeout("execution deadline exceeded")
            calls = bounded_call(lambda: self.planner.plan(query), min(self.planner_timeout, remaining))
            if not isinstance(calls, list) or len(calls) > 64 or any(not isinstance(call, ToolCall) for call in calls):
                raise ToolError("planner returned an invalid plan", "invalid_plan")
            stage = "execution"
            for call in calls[:self.max_steps]:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ExecutionTimeout("execution deadline exceeded")
                answer = self.registry.execute(call, timeout=remaining)
                trace.steps.append({"tool": call.name, "input": call.argument, "output": answer})
                label = call.argument if isinstance(call.argument, str) else next(iter(call.argument.values()), "")
                answers.append(f"[{label}] {answer}")
        except (ValueError, ExecutionError, ExecutionTimeout) as error:
            trace.error = str(error)
            code = getattr(error, "code", None)
            trace.error_code = f"{stage}_error" if code in (None, "execution_error") else code
            trace.error_stage = stage
        except Exception:
            trace.error = f"{stage} failed"
            trace.error_code = f"{stage}_error"
            trace.error_stage = stage
        trace.result = "\n".join(answers) or "No grounded answer available."
        if trace.error_code == "invalid_query":
            trace.result = ""
        trace.duration_ms = round((monotonic() - started) * 1000, 3)
        return trace

def make_agent(planner: Planner | None = None, *, timeout: float = 30,
               planner_timeout: float = 10, tool_timeout: float = 5) -> Agent:
    tools = ToolRegistry(timeout=tool_timeout)
    tools.register(
        "knowledge_lookup",
        lambda topic: DOCUMENTS[topic],
        parameters={
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(DOCUMENTS)}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        description="Read the refund, shipping, or support policy.",
    )
    return Agent(planner if planner is not None else DeterministicPlanner(), tools,
                 timeout=timeout, planner_timeout=planner_timeout)
