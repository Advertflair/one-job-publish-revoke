# OS-sandboxed Wasmtime worker

Each transform runs in a disposable macOS child process with a fresh Wasmtime store and zero imports. The child starts with an empty environment and only its standard streams. It loads the trusted runtime, installs a deny-by-default Seatbelt policy, then accepts and compiles one binary module and one document from stdin. A parent-side deadline and output cap bound the pipe exchange. A failed or unsupported sandbox rejects the job.

There are no WASI, network, file, storage, publish, or revoke imports. The host can stage the child's bounded result through the coordinator. The configured publisher still decides whether to publish those bytes. The child receives no coordinator handle or database path.

The guest exports fixed two-page 32-bit memory and a function called transform. The function takes the input length, output offset, and output capacity as i32 values and returns the output length as i32. Input starts at offset zero. Output starts at offset 65,536. Modules are limited to 128 KiB. Input and output are each limited to 64 KiB. There is one instance, one linear memory, no tables, and one million units of Wasmtime fuel, including any start function.

The worker requires Python 3.13 or later on macOS, with `os.waitid` and `WNOWAIT` support for observing exit without reaping the process-group leader. Unsupported runtimes reject the job before starting a subprocess. The Python minimum for the separate SQLite coordinator is unchanged.

Run the standalone tests in a trusted local macOS shell with a virtual environment. The SQLite coordinator is optional for these tests.

~~~sh
python3.13 -m venv /path/to/worker-venv
/path/to/worker-venv/bin/python -m pip install -r worker-requirements.txt
/path/to/worker-venv/bin/python -B -m unittest -v test_isolated_worker
~~~

The native probe verifies that temporary-file reads and writes, TCP connect and bind, UDP send, fork, and spawn work before sandboxing and fail with a permission error afterward. It uses only temporary fixtures, loopback, and a harmless executable. A separate test exercises the real worker entry point and asserts that file access is denied before the request is read. It also checks that an installation error prevents request handling. A sandboxed test runner may block installing a second Seatbelt policy. In that case the worker rejects rather than executing the module.

Every worker starts in its own process group. The parent tears down the group before reaping the leader, including on timeout and normal exit. A stand-in child tests both paths. This cleanup is a fallback for descendants in the same group. The sandbox is still responsible for denying process creation and preventing access to host resources.

This is a macOS research backend. It uses the deprecated Seatbelt library interface and deliberately rejects other platforms. It depends on a trusted Python interpreter, Wasmtime package, trusted worker script, and OS. The child has CPU, deadline, guest-memory, module-size, and pipe limits. These are not a total host-memory bound. The native probes validate the listed operations on the tested host. They do not cover every syscall or side channel. The host must authenticate callers, assign the correct input, and treat output as untrusted content.

[Wasmtime security model](https://docs.wasmtime.dev/security.html) · [Wasmtime Python API](https://bytecodealliance.github.io/wasmtime-py/)
