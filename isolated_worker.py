import os
from pathlib import Path
import selectors
import subprocess
import sys
from time import monotonic
from typing import TYPE_CHECKING

from worker_process import (
    FRAME, MAX_INPUT_BYTES, MAX_MODULE_BYTES, MAX_OUTPUT_BYTES, SUCCESS, WASM_HEADER,
)

if TYPE_CHECKING:
    from coordinator import Coordinator, Reply


WORKER_SCRIPT = Path(__file__).with_name("worker_process.py").resolve()
WORKER_TIMEOUT = 10
MAX_RESPONSE_BYTES = MAX_OUTPUT_BYTES + 1
ERRORS = {
    b"runtime": "Wasmtime is unavailable in the worker interpreter",
    b"sandbox": "worker OS sandbox could not be installed",
    b"frame": "worker rejected the request frame",
    b"imports": "worker imports are forbidden",
    b"exports": "worker must export only memory and transform",
    b"memory": "worker needs fixed two-page 32-bit memory",
    b"signature": "worker transform has an invalid signature",
    b"length": "worker returned an invalid output length",
    b"fuel": "worker exhausted its execution budget",
    b"trap": "worker execution trapped",
    b"module": "worker module or instance rejected",
}


class WorkerRejected(Exception):
    pass


def _exchange(child: subprocess.Popen, request: bytes) -> bytes:
    deadline = monotonic() + WORKER_TIMEOUT
    pending = memoryview(request)
    response = bytearray()
    with selectors.DefaultSelector() as selector:
        os.set_blocking(child.stdin.fileno(), False)
        selector.register(child.stdin, selectors.EVENT_WRITE)
        selector.register(child.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise WorkerRejected("worker exceeded its process deadline")
            for key, event in selector.select(remaining):
                if event == selectors.EVENT_WRITE:
                    try:
                        pending = pending[os.write(key.fd, pending):]
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        pending = pending[len(pending):]
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    chunk = os.read(key.fd, MAX_RESPONSE_BYTES + 1 - len(response))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        response.extend(chunk)
                        if len(response) > MAX_RESPONSE_BYTES:
                            raise WorkerRejected("worker process returned too many bytes")
    if child.wait(timeout=max(0, deadline - monotonic())) != 0:
        raise WorkerRejected("worker process exited unsuccessfully")
    return bytes(response)


def transform_document(wasm: bytes, document: bytes) -> bytes:
    if sys.platform != "darwin":
        raise WorkerRejected("OS sandbox unavailable on this platform")
    if (
        not isinstance(wasm, bytes)
        or len(wasm) > MAX_MODULE_BYTES
        or not wasm.startswith(WASM_HEADER)
    ):
        raise WorkerRejected("worker must be a bounded binary WebAssembly module")
    if not isinstance(document, bytes) or len(document) > MAX_INPUT_BYTES:
        raise WorkerRejected("worker input exceeds the allowed size or type")

    request = FRAME.pack(len(wasm), len(document)) + wasm + document
    try:
        with subprocess.Popen(
            [sys.executable, "-I", "-B", str(WORKER_SCRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd="/", env={}, close_fds=True,
        ) as child:
            try:
                response = _exchange(child, request)
            finally:
                if child.poll() is None:
                    child.kill()
    except subprocess.TimeoutExpired:
        raise WorkerRejected("worker exceeded its process deadline") from None
    except OSError:
        raise WorkerRejected("worker process could not communicate") from None

    if response.startswith(SUCCESS):
        return response[1:]
    raise WorkerRejected(ERRORS.get(response, "invalid worker process response"))


def transform_and_stage(
    coordinator: "Coordinator", *, job_id: str, worker: str, blob_id: str,
    wasm: bytes, document: bytes,
) -> "Reply":
    payload = transform_document(wasm, document)
    return coordinator.stage(job_id, worker, blob_id, payload)
