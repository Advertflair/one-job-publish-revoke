# One-job publication and revocation

**Decision:** Keep the atomic terminal decision and the single read gate for the next experiment. The finite model completed with no invariant violations. Both legitimate winners were reachable, and both deliberately broken variants produced the expected counterexamples.

| Run | TLC exit | Observed result |
| --- | ---: | --- |
| `Base.cfg` | 0 | 453 states generated, 164 distinct, zero queued, depth 8. All five invariants held. |
| `PublishWins.cfg` | 12 | Expected witness for `NoPublishedWitness`. Seal → publish → revoke returns `AlreadyPublished` → recipient receives the bound output. |
| `RevokeWins.cfg` | 12 | Expected witness for `NoRevokedWitness`. Revoke → publish returns `AlreadyRevoked` → recipient receives `NotPublished` with no bytes. |
| `SplitPublish.cfg` | 12 | Expected violation of `RevocationSticks`. Seal → publish checks `Running` → revoke commits → stale publish overwrites the terminal state. |
| `DirectRead.cfg` | 12 | Expected violation of `ReleaseRequiresCommit`. Seal → recipient obtains the staged output while the job is still `Running`. |

The two reachability configurations deliberately assert that their target outcome is impossible. TLC's violation trace is the evidence that the outcome is reachable. The two broken configurations each enable one fault independently.

## Scope

The model has one job, one successful seal at most, one publish attempt, one revoke attempt, a fixed recipient, and an outsider. Scheduling is nondeterministic. A publish attempt can precede sealing or supply a mismatched digest. Terminal retry replies and authorization rules are checked as functions of state. Read actions record the state and binding at release. Blob identity and digest are symbolic values. Durability, atomic transitions, and immutable storage are assumptions in this run. The model checks safety and reachability. It includes no crash, concurrent coordinators, clock, real storage, cryptographic verification, or guaranteed termination.

The next design gate is crash recovery. A crash before a durable change must leave the race open. A crash after a durable change but before the reply must preserve the winner. If the persistence primitive cannot support both outcomes, change the persistence design. If output can escape through a second read path, change the visibility boundary.

## Reproduce

Run from this directory with Java and a path to the TLC jar in `TLC_JAR`. The run used Java 21 and TLC reporting `2026.11.15.002146` at revision `bc1b506`.

```sh
for scenario in Base PublishWins RevokeWins SplitPublish DirectRead; do
  java -XX:+UseParallelGC -cp "$TLC_JAR" tlc2.TLC -workers 1 \
    -metadir "${TMPDIR:-/tmp}/one-job-tlc-$$-$scenario" \
    -noGenerateSpecTE -config "$scenario.cfg" OneJob.tla
  printf '%s exit=%s\n' "$scenario" "$?"
done
```

[Official TLC release](https://github.com/tlaplus/tlaplus/releases/tag/v1.8.0) · [Eclipse Temurin Java](https://adoptium.net/temurin/releases?version=21)
