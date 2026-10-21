import json
import sys

from .core import make_agent

if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "What is the refund policy?"
    trace = make_agent().run(query)
    response = {
        "question": trace.query,
        "steps": trace.steps,
        "result": trace.result,
        "error": trace.error,
    }
    print(json.dumps(response, indent=2))
