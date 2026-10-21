# Technical notes

## Execution path

`make_agent()` registers the policy lookup tool and returns an offline agent by default. A planner implements `plan(query)` and returns a list of `ToolCall` objects. Up to 64 planned calls can be transferred back; execution uses the first `max_steps` calls, with a default limit of three.

The planner runs in a spawned worker. The registry validates each selected call in the parent before executing the tool in another worker. Only successfully completed calls are appended to `Trace.steps`.

The answer is assembled from successful outputs, each prefixed by its argument label. An error stops execution while retaining earlier steps. No answer from the model is inserted into the trace result.

## Schemas

`ToolRegistry.register()` takes a callable, optional JSON Schema parameters, and a description. Schemas must describe an object and pass the JSON Schema meta schema check. Schema definitions are trusted application configuration, not model supplied input.

JSON Schema validation is performed by `jsonschema.Draft202012Validator`. The default lookup schema requires one `topic` string, restricts it to the three dataset keys, and sets `additionalProperties` to false. Validation happens before a worker can execute the tool.

Argument payloads must serialize as JSON without nonfinite numbers and fit within 16,000 serialized characters. Legacy string arguments have a 1,000 character limit. Tool output must be a string no longer than 16,000 characters.

Tools without an explicit schema receive a single `value` string field and retain the original positional string callable convention. A string passed to a tool with one schema property is normalized to that property. Tools with explicit schemas receive validated keyword arguments.

```python
from agentlab.core import ToolCall, ToolRegistry

if __name__ == "__main__":
    tools = ToolRegistry(timeout=2)
    tools.register(
        "uppercase",
        lambda text: text.upper(),
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 100}},
            "required": ["text"],
            "additionalProperties": False,
        },
    )
    print(tools.execute(ToolCall("uppercase", {"text": "hello"})))
```

## Optional LLM planner

`LLMPlanner` requires an explicit model name and a registry. It sends strict function definitions to `https://api.openai.com/v1/responses` through a replaceable transport. The default HTTP transport uses Python's standard library and reads `OPENAI_API_KEY` only when making a request.

The request sets `store` to false, supplies policy planning instructions, and limits output tokens. It does not retry. The HTTP request has its own socket timeout, while the agent enforces the outer wall clock budget by terminating the planner worker.

The parser requires a completed response. It ignores reasoning and ordinary text messages, rejects refusals and unsupported output items, parses JSON function arguments, rejects duplicate JSON property names, and validates every returned call through the registry before returning a plan.

An invalid call anywhere in the model batch rejects the batch before any call runs. The runtime validates calls again immediately before execution. API exceptions do not expose response bodies, authorization headers, or credentials in trace errors.

```python
from agentlab.core import make_agent
from agentlab.planners import LLMPlanner

if __name__ == "__main__":
    agent = make_agent()
    agent.planner = LLMPlanner("your-function-calling-model", agent.registry)
    print(agent.run("When can I get my money back?").result)
```

This example needs a real model name and a credential in the process environment. No live API is used by the test suite.

## Worker lifecycle

`bounded_call()` serializes a trusted callable with cloudpickle, starts a daemon worker using multiprocessing spawn, and waits on a pipe only until the deadline. A result larger than the IPC limit is rejected. Worker crashes and missing results produce execution errors.

The finally block closes the pipe, terminates a live worker, and joins it. If termination does not complete within one second, it kills the worker and waits up to another second. Deadline timings include serialization, startup, execution, and result transfer; cleanup can extend the observed elapsed time.

This is process lifetime control, not a security sandbox. The worker has the parent's filesystem and network permissions. Pickle payloads are generated locally from registered application code and must never come from an untrusted source. A tool can create external side effects before it times out; terminating it cannot undo those effects or guarantee termination of external subprocess descendants.

Each phase runs from a serialized snapshot. In memory mutations in a planner or tool do not synchronize back to the parent. Use return values or an explicit external store for state. Applications must use the normal multiprocessing main guard.

## Deadlines and limits

`timeout` bounds the entire run. `planner_timeout` caps the planning phase. `ToolRegistry.timeout` caps an individual tool, further limited by the run time remaining. All timeout settings must be positive finite numbers. `max_steps` must be a nonnegative integer.

Input queries are capped at 16,000 characters. Blank or invalid input fails before planning. A zero step limit permits planning but executes no calls. Excess planned calls are not executed.

## Trace and error contract

The Python trace retains `query`, `steps`, `result`, and `error`. It also reports `error_code`, `error_stage`, and `duration_ms`. The CLI renames `query` to `question` and exits with status 1 when a run returns an error.

| Error code | Meaning |
| :--- | :--- |
| `invalid_query` | Blank, invalid, or oversized question |
| `invalid_plan` | Planner result is not a bounded list of tool calls |
| `tool_not_allowed` | Tool name is absent from the registry |
| `invalid_arguments` | Arguments fail the schema or payload checks |
| `invalid_output` | Tool result violates the string output contract |
| `timeout` | Planner, tool, or run deadline elapsed |
| `planner_error` | Provider request, response, or argument parsing failed |
| `planning_error` | Unexpected planning worker failure |
| `execution_error` | Unexpected tool or worker failure |

Error stages distinguish planning from execution. Unexpected exception details are replaced with generic messages. Registered tools may return their own data, so their outputs should still be treated according to the application's data handling requirements.

## Validation evidence

Tests run the planner against mocked Responses API payloads and mock the HTTP boundary separately. They cover unknown tools, additional arguments, missing and mistyped fields, duplicate JSON keys, incomplete responses, refusals, HTTP errors, and credential absence.

Runtime tests cover worker crashes, deadlines, delayed file writes after timeout, exact grounded output, partial failure, schema boundaries, and step limits. Evaluation scoring also has negative tests so fabricated answers and unauthorized completed calls fail the corresponding checks.
