from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from coordinator import Binding, Coordinator, MAX_OUTPUT_BYTES, Reply


PAYLOAD = b"example output"
BINDING = Binding("blob", sha256(PAYLOAD).hexdigest(), "recipient")
OTHER = Binding("blob", "0" * 64, "recipient")
CRASH_CHILD = """
import os
import sqlite3
import sys
from unittest.mock import patch
from coordinator import Binding, Coordinator

database, cut, digest = sys.argv[1:]
connect = sqlite3.connect

class CrashConnection(sqlite3.Connection):
    def commit(self):
        row = self.execute(
            "SELECT state, blob_id, digest, recipient FROM jobs WHERE job_id = 'job'"
        ).fetchone()
        if not self.in_transaction or tuple(row) != ("Published", "blob", digest, "recipient"):
            raise AssertionError("crash point did not witness the intended pending publication")
        if cut == "after":
            super().commit()
        os._exit(23)

def crash_connect(*args, **kwargs):
    return connect(*args, factory=CrashConnection, **kwargs)

with patch("coordinator.sqlite3.connect", crash_connect):
    Coordinator(database).publish("job", "publisher", Binding("blob", digest, "recipient"))
raise SystemExit("publish returned before the crash hook")
"""


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "job.sqlite"
        self.core = Coordinator(self.database)
        self.core.initialize()
        self.core.create_job(
            "job", worker="worker", publisher="publisher", revoker="revoker",
            recipient="recipient",
        )

    def seal(self):
        self.assertEqual(self.core.stage("job", "worker", "blob", PAYLOAD), Reply("Sealed"))

    def publish(self):
        return self.core.publish("job", "publisher", BINDING)

    def revoke(self):
        return self.core.revoke("job", "revoker")

    def read(self):
        return self.core.read("job", "recipient")

    def assert_denied(self):
        for job in ("job", "missing"):
            self.assertEqual(self.core.read(job, "outsider"), Reply("Denied"))
            self.assertEqual(self.core.status(job, "outsider"), Reply("Denied"))
            self.assertEqual(self.core.publish(job, "worker", BINDING), Reply("Denied"))
            self.assertEqual(self.core.revoke(job, "worker"), Reply("Denied"))

    def test_publish_and_read_gate(self):
        self.assert_denied()
        self.assertEqual(self.publish(), Reply("NotReady"))
        self.seal()
        self.assertEqual(self.read(), Reply("NotPublished"))
        self.assertEqual(self.core.read("job", "worker"), Reply("Denied"))
        self.assertEqual(self.core.publish("job", "publisher", OTHER), Reply("DigestMismatch"))
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        self.assertEqual(self.core.publish("job", "publisher", OTHER), Reply("Conflict"))
        self.assertEqual(self.revoke(), Reply("AlreadyPublished"))
        self.assertEqual(self.read(), Reply("Released", BINDING, PAYLOAD))
        self.assert_denied()

    def test_revoke_does_not_require_output(self):
        self.assertEqual(self.revoke(), Reply("Revoked"))
        self.assertEqual(self.revoke(), Reply("Revoked"))
        self.assertEqual(self.publish(), Reply("AlreadyRevoked"))
        self.assertEqual(self.read(), Reply("NotPublished"))
        self.assert_denied()

    def test_worker_cannot_share_another_role(self):
        for role in ("publisher", "revoker", "recipient"):
            with self.subTest(role=role):
                principals = {name: name for name in ("worker", "publisher", "revoker", "recipient")}
                principals[role] = "worker"
                with self.assertRaisesRegex(ValueError, "worker must be distinct"):
                    self.core.create_job("invalid", **principals)
                self.assertEqual(self.core.status("invalid", "recipient"), Reply("Denied"))

    def test_unauthorized_writes_do_not_need_the_write_lock(self):
        with closing(sqlite3.connect(self.database, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.core.stage("job", "outsider", "blob", PAYLOAD), Reply("Denied"))
            self.assertEqual(self.core.publish("job", "outsider", BINDING), Reply("Denied"))
            self.assertEqual(self.core.revoke("job", "outsider"), Reply("Denied"))
        self.assertEqual(self.core.status("job", "recipient"), Reply("Running"))

    def test_write_connection_sync_settings(self):
        with closing(self.core._connect()) as db:
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 3)
            if sys.platform == "darwin":
                self.assertEqual(db.execute("PRAGMA fullfsync").fetchone()[0], 1)

    def test_staging_is_bounded_and_immutable(self):
        self.assertEqual(self.core.stage("job", "recipient", "blob", PAYLOAD), Reply("Denied"))
        self.assertEqual(
            self.core.stage("job", "worker", "blob", bytes(MAX_OUTPUT_BYTES + 1)),
            Reply("InvalidOutput"),
        )
        self.seal()
        self.seal()
        self.assertEqual(self.core.stage("job", "worker", "other", PAYLOAD), Reply("Conflict"))
        self.assertEqual(self.core.stage("job", "worker", "blob", b"other"), Reply("Conflict"))
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        self.assertEqual(self.core.stage("job", "worker", "blob", PAYLOAD), Reply("Closed"))

    def test_corrupted_bytes_are_never_released(self):
        self.seal()
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE jobs SET payload = ?", (b"corrupted",))
        with self.assertLogs("coordinator", level="ERROR") as captured:
            self.assertEqual(self.read(), Reply("Unavailable"))
        self.assertIn("integrity verification", captured.output[0])

    def test_missing_database_does_not_recreate_records(self):
        self.database.unlink()
        with self.assertLogs("coordinator", level="ERROR"):
            self.assertEqual(self.publish(), Reply("OutcomeUnknown"))
            self.assertEqual(self.revoke(), Reply("OutcomeUnknown"))
            self.assertEqual(self.read(), Reply("Unavailable"))
        self.assertFalse(self.database.exists())

    def run_crash_child(self, cut):
        return subprocess.run(
            [sys.executable, "-c", CRASH_CHILD, str(self.database), cut, BINDING.digest],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def crash_publish(self, cut):
        self.seal()
        child = self.run_crash_child(cut)
        self.assertEqual(child.returncode, 23, child.stdout + child.stderr)
        self.core = Coordinator(self.database)

    def test_crash_probe_rejects_a_write_without_pending_publication(self):
        child = self.run_crash_child("before")
        self.assertNotEqual(child.returncode, 23)
        self.assertIn("did not witness the intended pending publication", child.stderr)
        self.assertEqual(self.core.status("job", "recipient"), Reply("Running"))

    def test_precommit_crash_retry_first(self):
        self.crash_publish("before")
        self.assertEqual(self.core.status("job", "recipient"), Reply("Running"))
        self.assertEqual(self.read(), Reply("NotPublished"))
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        self.assertEqual(self.revoke(), Reply("AlreadyPublished"))
        self.assertEqual(self.read(), Reply("Released", BINDING, PAYLOAD))

    def test_precommit_crash_revoke_first(self):
        self.crash_publish("before")
        self.assertEqual(self.revoke(), Reply("Revoked"))
        self.assertEqual(self.publish(), Reply("AlreadyRevoked"))
        self.assertEqual(self.read(), Reply("NotPublished"))

    def test_postcommit_crash_retry_first(self):
        self.crash_publish("after")
        self.assertEqual(self.core.status("job", "recipient"), Reply("Published"))
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        self.assertEqual(self.revoke(), Reply("AlreadyPublished"))
        self.assertEqual(self.read(), Reply("Released", BINDING, PAYLOAD))

    def test_postcommit_crash_revoke_first(self):
        self.crash_publish("after")
        self.assertEqual(self.revoke(), Reply("AlreadyPublished"))
        self.assertEqual(self.publish(), Reply("Published", BINDING))
        self.assertEqual(self.read(), Reply("Released", BINDING, PAYLOAD))

    def test_concurrent_retry_and_revoke_after_crash(self):
        self.crash_publish("before")
        gate = Barrier(2, timeout=5)

        def race(operation):
            gate.wait()
            return operation()

        with ThreadPoolExecutor(max_workers=2) as pool:
            publish = pool.submit(race, self.publish)
            revoke = pool.submit(race, self.revoke)
            replies = (publish.result(), revoke.result())
        if replies[0].code == "Published":
            self.assertEqual(replies, (Reply("Published", BINDING), Reply("AlreadyPublished")))
            self.assertEqual(self.read(), Reply("Released", BINDING, PAYLOAD))
        else:
            self.assertEqual(replies, (Reply("AlreadyRevoked"), Reply("Revoked")))
            self.assertEqual(self.read(), Reply("NotPublished"))


if __name__ == "__main__":
    unittest.main()
