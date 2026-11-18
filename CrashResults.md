# One-job crash recovery

TLC checked all 14 reachable states. It generated 28 states, emptied the queue, and found no invariant violations. Each of the four rows below was independently witnessed by TLC.

| Crash point | First operation after restart | Final record | Replies |
| --- | --- | --- | --- |
| Before durable publish | Retry | Published | Retry succeeds. Revoke returns `AlreadyPublished`. |
| Before durable publish | Revoke | Revoked | Revoke succeeds. Retry returns `AlreadyRevoked`. |
| After durable publish, before the reply | Retry | Original publication | Retry returns the existing success. Revoke returns `AlreadyPublished`. |
| After durable publish, before the reply | Revoke | Original publication | Revoke returns `AlreadyPublished`. Retry returns the existing success. |

Every completed race made exactly one terminal write. The published record binds the same symbolic blob ID, digest, and recipient after recovery.

**Broken control:** In the lossy variant, the original write commits and the next crash resets the record to `Running`. TLC stopped at that crash with a violation of `DurableDecisionSurvives`.

**Decision:** Keep the first-durable-decision rule. Any candidate store must demonstrate that its terminal conditional write survives process failure and restart, including when the client receives no reply. If a committed record disappears or can be overwritten, change the persistence design before relying on the contract.

The extension starts with an authorized publish already eligible to commit. It models one coordinator, one crash, one restart, one identical retry, and one authorized revoke. It exhausts the order of their terminal decisions. It assumes a durable record with atomic conditional writes and an immutable sealed blob. It does not model real storage, repeated crashes, replicas, asynchronous response delivery, or reader recovery. The base model covers the read gate separately.

## Reproduce

With Java 21 and an absolute path to the TLC jar in `TLC_JAR`, run from the repository root:

~~~sh
for scenario in CrashRecovery BeforeRetryFirst BeforeRevokeFirst AfterRetryFirst AfterRevokeFirst LoseDurableWrite; do
  java -XX:+UseParallelGC -cp "$TLC_JAR" tlc2.TLC -workers 1 \
    -metadir "${TMPDIR:-/tmp}/one-job-tlc-$$-$scenario" \
    -noGenerateSpecTE -config "$scenario.cfg" CrashRecovery.tla
  printf '%s exit=%s\n' "$scenario" "$?"
done
~~~

Expect exit 0 for `CrashRecovery`. Each reachability probe deliberately denies one desired witness and should exit 12 naming its corresponding `No…First` invariant. The lossy control should exit 12 naming `DurableDecisionSurvives`. Parse errors or other invariant violations are unexpected.
