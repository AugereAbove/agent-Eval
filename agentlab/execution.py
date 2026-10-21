from __future__ import annotations

import math
import multiprocessing
from time import monotonic
from typing import Callable

import cloudpickle

class ExecutionError(RuntimeError):
    def __init__(self, message: str, code: str = "execution_error"):
        super().__init__(message)
        self.code = code

class ExecutionTimeout(TimeoutError):
    code = "timeout"

def check_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return float(value)

def _worker(connection, payload):
    try:
        function = cloudpickle.loads(payload)
        value = function()
        message = (True, value)
    except Exception as error:
        code = getattr(error, "code", "execution_error")
        text = str(error) if code != "execution_error" else "worker execution failed"
        message = (False, code, text)
    try:
        connection.send_bytes(cloudpickle.dumps(message))
    finally:
        connection.close()

def bounded_call(function: Callable, timeout: float):
    deadline = monotonic() + check_timeout(timeout)
    payload = cloudpickle.dumps(function)
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(sender, payload), daemon=True)
    started = False
    try:
        process.start()
        started = True
        sender.close()
        remaining = deadline - monotonic()
        if remaining <= 0 or not receiver.poll(remaining):
            raise ExecutionTimeout("execution deadline exceeded")
        try:
            message = cloudpickle.loads(receiver.recv_bytes(maxlength=2_000_000))
        except (EOFError, OSError) as error:
            raise ExecutionError("worker exited without a result") from error
        if monotonic() > deadline:
            raise ExecutionTimeout("execution deadline exceeded")
        if not message[0]:
            raise ExecutionError(message[2], message[1])
        return message[1]
    finally:
        receiver.close()
        sender.close()
        if started:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            process.close()
