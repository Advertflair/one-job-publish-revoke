from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import wasmtime

from isolated_worker import (
    MAX_INPUT_BYTES, MAX_MODULE_BYTES, WorkerRejected, transform_and_stage,
    transform_document, WORKER_SCRIPT,
)


def worker(body, extra="", memory="2 2", imports=""):
    return bytes(wasmtime.wat2wasm(f"""
        (module
            {imports}
            (memory (export "memory") {memory})
            {extra}
            (func (export "transform") (param $size i32) (param $output i32)
                (param $capacity i32) (result i32)
                {body}))
    """))


COPY_INPUT = worker("local.get $output i32.const 0 local.get $size memory.copy local.get $size")

OS_PROBE = """
import errno
from pathlib import Path
import runpy
import socket
import sys

script, fixture, write_target, port = sys.argv[1:]
port = int(port)
isolate = runpy.run_path(script)["isolate"]

def connect():
    with socket.create_connection(("127.0.0.1", port), timeout=1):
        pass

def bind():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))

def datagram():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(b"probe", ("127.0.0.1", port))

write_target = Path(write_target)
fixture = Path(fixture)
assert fixture.read_bytes() == b"parent only"
write_target.write_bytes(b"baseline")
write_target.unlink()
connect()
bind()
datagram()
isolate()
for operation in (
    fixture.read_bytes,
    lambda: write_target.write_bytes(b"denied"),
    connect,
    bind,
    datagram,
):
    try:
        operation()
    except OSError as error:
        assert error.errno in (errno.EACCES, errno.EPERM), error
    else:
        raise AssertionError("sandbox allowed an operation")
print("native file and network operations denied")
"""


class IsolatedWorkerTests(unittest.TestCase):
    def test_only_assigned_input_is_returned(self):
        document = bytes(range(256)) * 256
        self.assertEqual(transform_document(COPY_INPUT, document), document)
        self.assertEqual(transform_document(COPY_INPUT, b""), b"")

    def test_memory_is_fresh_for_each_job(self):
        module = worker("local.get $output i32.const 1 i32.load8_u i32.store8 i32.const 1")
        self.assertEqual(transform_document(module, b"ab"), b"b")
        self.assertEqual(transform_document(module, b"a"), b"\x00")

    def test_all_imports_are_forbidden(self):
        for namespace, name in (
            ("wasi_snapshot_preview1", "path_open"),
            ("wasi_snapshot_preview1", "sock_open"),
            ("host", "publish"),
        ):
            with self.subTest(namespace=namespace, name=name):
                module = worker("i32.const 0", imports=f'(import "{namespace}" "{name}" (func))')
                with self.assertRaisesRegex(WorkerRejected, "imports are forbidden"):
                    transform_document(module, b"")

    def test_bounds_and_export_signature(self):
        with self.assertRaisesRegex(WorkerRejected, "input exceeds"):
            transform_document(COPY_INPUT, b"0" * (MAX_INPUT_BYTES + 1))
        with self.assertRaisesRegex(WorkerRejected, "bounded binary"):
            transform_document(COPY_INPUT + bytes(MAX_MODULE_BYTES), b"")
        with self.assertRaisesRegex(WorkerRejected, "fixed two-page"):
            transform_document(worker("i32.const 0", memory="2 3"), b"")
        signature = bytes(wasmtime.wat2wasm(
            '(module (memory (export "memory") 2 2) (func (export "transform")))'
        ))
        with self.assertRaisesRegex(WorkerRejected, "invalid signature"):
            transform_document(signature, b"")
        for length in ("-1", "65537"):
            with self.subTest(length=length):
                with self.assertRaisesRegex(WorkerRejected, "invalid output length"):
                    transform_document(worker(f"i32.const {length}"), b"")

    def test_fuel_limits_start_and_transform(self):
        loop = "(loop $spin br $spin)"
        for module in (
            worker(f"{loop} i32.const 0"),
            worker("i32.const 0", extra=f"(func $start {loop}) (start $start)"),
        ):
            with self.assertRaisesRegex(WorkerRejected, "exhausted its execution budget"):
                transform_document(module, b"")

    def test_worker_output_can_only_be_staged(self):
        core = Mock()
        args = dict(job_id="job", worker="worker", blob_id="blob", document=b"document")
        with self.assertRaises(WorkerRejected):
            transform_and_stage(core, wasm=worker("unreachable"), **args)
        self.assertEqual(core.mock_calls, [])
        self.assertIs(transform_and_stage(core, wasm=COPY_INPUT, **args), core.stage.return_value)
        self.assertEqual(len(core.mock_calls), 1)
        core.stage.assert_called_once_with("job", "worker", "blob", b"document")

    def test_native_os_sandbox_denies_file_and_network_access(self):
        with TemporaryDirectory() as directory, socket.socket() as listener:
            fixture = Path(directory) / "private"
            fixture.write_bytes(b"parent only")
            write_target = Path(directory) / "would-be-created"
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            child = subprocess.run(
                [sys.executable, "-I", "-B", "-c", OS_PROBE, str(WORKER_SCRIPT),
                 str(fixture), str(write_target), str(port)],
                cwd="/", env={}, capture_output=True, text=True, timeout=10, close_fds=True,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), "native file and network operations denied")
            self.assertFalse(write_target.exists())

    def test_unsupported_sandbox_fails_closed(self):
        with patch("isolated_worker.sys.platform", "unsupported"):
            with self.assertRaisesRegex(WorkerRejected, "sandbox unavailable"):
                transform_document(COPY_INPUT, b"")

    def test_parent_caps_worker_output_and_time(self):
        with TemporaryDirectory() as directory:
            script = Path(directory) / "worker.py"
            with patch("isolated_worker.WORKER_SCRIPT", script):
                script.write_text("import sys; sys.stdout.buffer.write(b'x' * 100000)\n")
                with self.assertRaisesRegex(WorkerRejected, "too many bytes"):
                    transform_document(COPY_INPUT, b"")
                script.write_text("import time; time.sleep(5)\n")
                with patch("isolated_worker.WORKER_TIMEOUT", 0.5):
                    with self.assertRaisesRegex(WorkerRejected, "process deadline"):
                        transform_document(COPY_INPUT, b"")


if __name__ == "__main__":
    unittest.main()
