# Contributing

Keep changes small enough to explain and test. This project stays useful when the execution path is easy to follow.

## Local setup

Create and activate a virtual environment with Python 3.10 or newer, then install the package:

```bash
python -m pip install -e ".[test]"
```

Before opening a pull request, run:

```bash
python -m pytest -q
python -m agentlab.evaluate
python -m agentlab "How do refunds and shipping work?"
```

## Making a change

Preserve the existing JSON fields and error strings unless the change explicitly updates that contract. Add a focused regression test for a bug fix. Update the README or technical notes when observable behavior changes.

Use descriptive names and ordinary Python control flow. Avoid extra dependencies for work the standard library can handle. Source files currently use no comments or docstrings; longer explanations belong in `docs/design.md`.

A pull request should explain the problem, the resulting behavior, and how the change was checked. Include an input and expected output when it helps reproduce the issue.

## Reporting a problem

Include your operating system, Python version, exact command or short code example, expected behavior, and actual output. Remove credentials and private data before posting a trace.
