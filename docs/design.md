# Technical notes

## Execution path

`make_agent()` builds a fresh registry, registers `knowledge_lookup`, and returns an `Agent` with a `DeterministicPlanner`. Each agent gets its own registry. The policy dictionary is shared at module scope.

The planner lowercases the query and checks each key in `DOCUMENTS`. Matching is substring based, so `refunds` and `nonrefundable` both match `refund`. It does not interpret negation or intent. Matches retain dictionary order and become at most three `ToolCall` objects.

`Agent.run()` rejects blank input before planning. It slices the plan using `max_steps`, executes each call, and records successful results. A `ValueError` stops the loop. Earlier results remain in the trace; the failed call does not appear in `steps`.

The final answer joins successful results with newlines. Each line starts with the call argument in square brackets. When no call produces an answer, the result is `No grounded answer available.` Blank input returns an empty result with `empty query` in the error field.

## Public types

| Name | Fields or methods |
| :--- | :--- |
| `ToolCall` | Frozen dataclass with `name` and `argument` |
| `Trace` | Dataclass with `query`, `steps`, `result`, and optional `error` |
| `ToolRegistry` | `register(name, tool)` and `execute(call)` |
| `DeterministicPlanner` | `plan(query)` returns a list of tool calls |
| `Agent` | `run(query)` returns a trace; `max_steps` defaults to 3 |
| `make_agent` | Creates the default planner and lookup registry |

A trace step has three string fields: `tool`, `input`, and `output`. Error messages are strings, not structured error codes.

## Adding a tool

This example runs independently of the default policy agent:

```python
from agentlab.core import ToolCall, ToolRegistry

registry = ToolRegistry()
registry.register("uppercase", str.upper)

result = registry.execute(ToolCall("uppercase", "hello"))
assert result == "HELLO"
```

Names must be unique. The registry checks the name before checking argument length. Arguments of exactly 1,000 characters are accepted; longer arguments raise `ValueError`.

Tools are ordinary synchronous Python callables. Type hints describe the expected string input and output, but there is no runtime type validation beyond the name and length checks. Only register functions you intend to execute.

## Replacing the planner

The agent calls `plan(query)` on its planner. A small custom planner can supply explicit calls:

```python
from agentlab.core import Agent, ToolCall, ToolRegistry

class UppercasePlanner:
    def plan(self, query: str) -> list[ToolCall]:
        return [ToolCall("uppercase", query)]

registry = ToolRegistry()
registry.register("uppercase", str.upper)
agent = Agent(UppercasePlanner(), registry, max_steps=1)

trace = agent.run("hello")
assert trace.result == "[hello] HELLO"
```

The constructor annotation names `DeterministicPlanner`, while runtime dispatch accepts an object with a compatible `plan` method. There is no provider adapter or retry loop. A model integration would also need validation of returned calls, timeout handling, usage accounting, and separate evaluation cases.

## Evaluation contract

`evaluate()` creates one default agent and runs the entries in `CASES`. Each entry contains a question, an expected answer fragment, and a minimum completed call count.

A case passes only when the answer contains the fragment, enough calls completed, and `trace.error` is `None`. The return value contains `passed`, `total`, and `cases`. Each case contains its prompt, pass flag, and completed call count.

The module command serializes that return value to `evaluation-report.json` and stdout. It does not use a failing process exit code when a case fails; the test suite asserts that all fixed cases pass.

## Failure boundaries

The registry does not isolate tools or restrict filesystem and network access inside registered functions. The step limit caps the number of calls, not the duration or resource use of an individual call.

`Agent.run()` catches `ValueError` from execution and trace assembly. Planner exceptions and other tool exceptions propagate. The CLI prints returned errors in JSON and otherwise follows normal Python exception handling.

`max_steps` is not validated. Nonnegative integers are the intended input. Negative values currently use Python's negative slice behavior, and incompatible types raise an exception.

The default CLI also treats an empty argument string as a request for its default question. Use the Python API to test a truly empty query.
