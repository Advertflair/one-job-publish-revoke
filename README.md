# One-job publish and revoke

A small TLA+ experiment for a document worker that can stage an output privately. The coordinator can publish one exact output to a fixed recipient or revoke the job. The first durable terminal decision wins.

- [Base model and results](Results.md) cover both winners and deliberately broken publication and read paths.
- [Crash recovery model and results](CrashResults.md) cover a crash before or after the durable write and both orders of retry and revoke after restart.

## Contributions

Reproducible counterexamples, stronger invariants, and corrections to the stated assumptions are welcome. Open a GitHub issue for a testable claim, or a pull request with a narrow change. Include the TLC version, configuration, command, expected result, and smallest observed trace or state count. Maintainers should accept changes only after independently rerunning the relevant cases and reviewing the stated assumptions. Please use verified commit signatures for code contributions. Do not submit private data, production identifiers, or live-system tests.

This repository contains abstract models. Its results apply to the stated assumptions and finite state spaces.
