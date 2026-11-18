# One-job publish and revoke

A small TLA+ experiment for a document worker that can stage an output privately. The coordinator can publish one exact output to a fixed recipient or revoke the job. The first durable terminal decision wins.

- [Base model and results](Results.md) cover both winners and deliberately broken publication and read paths.
- [Crash recovery model and results](CrashResults.md) cover a crash before or after the durable write and both orders of retry and revoke after restart.

## Local reference implementation

[coordinator.py](coordinator.py) implements the one-job contract using SQLite and the Python standard library. Run the focused checks with `python3 -B -m unittest -v test_coordinator` from this directory. Tests stop a child process on either side of the SQLite commit, reopen the database, and check both retry/revoke orders. A separate test starts both operations concurrently after a pre-commit crash.

A trusted host provisions the database with `Coordinator.initialize()` and assigns worker, publisher, revoker, and recipient through `create_job()`. The worker must be distinct from the other three roles and can only stage its bounded output. Write operations check authorization before taking the SQLite write lock and recheck it in the write transaction. A trusted publisher supplies the intended blob ID, SHA-256 digest, and configured recipient. A single SQLite transaction serializes each publication or revocation. Reads require the assigned recipient and a published record, and verify the exact bytes before release. The recipient can query status. After an ambiguous write, the original caller can safely resubmit the same operation.

This is a local reference core for Python 3.10 or later. Integrators must authenticate caller identities before invoking it, keep the SQLite files private, and isolate the worker. There is no HTTP service or worker sandbox here. SQLite uses DELETE journaling with EXTRA synchronization and enables fullfsync on macOS. Tests witness a pending publication before killing the child on either side of commit. They cover process interruption on a local filesystem. Power-loss durability, replica failover, and implementation correctness beyond these tests remain unverified.

## Contributions

Reproducible counterexamples, stronger invariants, and corrections to the stated assumptions are welcome. Open a GitHub issue for a testable claim, or a pull request with a narrow change. Include the TLC version, configuration, command, expected result, and smallest observed trace or state count. Maintainers should accept changes only after independently rerunning the relevant cases and reviewing the stated assumptions. Please use verified commit signatures for code contributions. Do not submit private data, production identifiers, or live-system tests.

This repository contains abstract models. Its results apply to the stated assumptions and finite state spaces.
