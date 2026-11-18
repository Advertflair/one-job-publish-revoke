from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import logging
from pathlib import Path
import sqlite3
import sys


MAX_OUTPUT_BYTES = 1024 * 1024
LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY NOT NULL,
    worker TEXT NOT NULL,
    publisher TEXT NOT NULL,
    revoker TEXT NOT NULL,
    recipient TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'Running'
        CHECK (state IN ('Running', 'Published', 'Revoked')),
    blob_id TEXT,
    digest TEXT,
    payload BLOB,
    CHECK (
        (blob_id IS NULL AND digest IS NULL AND payload IS NULL)
        OR (blob_id IS NOT NULL AND digest IS NOT NULL AND payload IS NOT NULL)
    ),
    CHECK (state != 'Published' OR payload IS NOT NULL)
)
"""


@dataclass(frozen=True)
class Binding:
    blob_id: str
    digest: str
    recipient: str


@dataclass(frozen=True)
class Reply:
    code: str
    binding: Binding | None = None
    payload: bytes | None = None


class Coordinator:
    def __init__(self, database: str | Path):
        self._path = Path(database).resolve()

    def initialize(self) -> None:
        with closing(sqlite3.connect(self._path, isolation_level=None)) as db:
            if db.execute("PRAGMA journal_mode = DELETE").fetchone()[0] != "delete":
                raise sqlite3.OperationalError("could not enable rollback journaling")
            self._configure_sync(db)
            db.execute(SCHEMA)

    @staticmethod
    def _configure_sync(db: sqlite3.Connection) -> None:
        db.execute("PRAGMA synchronous = EXTRA")
        if sys.platform == "darwin":
            db.execute("PRAGMA fullfsync = ON")
            if db.execute("PRAGMA fullfsync").fetchone()[0] != 1:
                raise sqlite3.OperationalError("could not enable fullfsync")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise sqlite3.OperationalError("unexpected journal mode")
            self._configure_sync(db)
            return db
        except BaseException:
            db.close()
            raise

    @contextmanager
    def _write(self):
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()

    @staticmethod
    def _job(db: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
        return db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()

    def _may_write(self, job_id: str, actor: str, role: str) -> bool:
        with closing(self._connect()) as db:
            roles = db.execute(
                "SELECT worker, publisher, revoker FROM jobs WHERE job_id = ?", (job_id,),
            ).fetchone()
            return roles is not None and actor == roles[role]

    @staticmethod
    def _binding(job: sqlite3.Row) -> Binding:
        return Binding(job["blob_id"], job["digest"], job["recipient"])

    @staticmethod
    def _intact(job: sqlite3.Row) -> bool:
        payload = job["payload"]
        return isinstance(payload, bytes) and sha256(payload).hexdigest() == job["digest"]

    def create_job(
        self, job_id: str, *, worker: str, publisher: str, revoker: str, recipient: str
    ) -> None:
        if any(not isinstance(value, str) or not value for value in
               (job_id, worker, publisher, revoker, recipient)):
            raise ValueError("job ID and principals must be nonempty strings")
        if worker in (publisher, revoker, recipient):
            raise ValueError("worker must be distinct from publisher, revoker, and recipient")
        with self._write() as db:
            db.execute(
                "INSERT INTO jobs (job_id, worker, publisher, revoker, recipient) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, worker, publisher, revoker, recipient),
            )

    def stage(self, job_id: str, actor: str, blob_id: str, payload: bytes) -> Reply:
        try:
            if not self._may_write(job_id, actor, "worker"):
                return Reply("Denied")
            with self._write() as db:
                job = self._job(db, job_id)
                if job is None or actor != job["worker"]:
                    return Reply("Denied")
                if job["state"] != "Running":
                    return Reply("Closed")
                if not isinstance(payload, bytes) or len(payload) > MAX_OUTPUT_BYTES:
                    return Reply("InvalidOutput")
                if not isinstance(blob_id, str) or not 0 < len(blob_id) <= 128:
                    return Reply("InvalidOutput")
                digest = sha256(payload).hexdigest()
                if job["payload"] is not None:
                    if (blob_id, payload) != (job["blob_id"], job["payload"]):
                        return Reply("Conflict")
                    return Reply("Sealed")
                db.execute(
                    "UPDATE jobs SET blob_id = ?, digest = ?, payload = ? WHERE job_id = ?",
                    (blob_id, digest, payload, job_id),
                )
                return Reply("Sealed")
        except sqlite3.Error:
            LOGGER.exception("stage storage operation failed")
            return Reply("OutcomeUnknown")

    def publish(self, job_id: str, actor: str, expected: Binding) -> Reply:
        try:
            if not self._may_write(job_id, actor, "publisher"):
                return Reply("Denied")
            with self._write() as db:
                job = self._job(db, job_id)
                if job is None or actor != job["publisher"]:
                    return Reply("Denied")
                if job["state"] == "Revoked":
                    return Reply("AlreadyRevoked")
                if job["state"] == "Published":
                    binding = self._binding(job)
                    return Reply("Published", binding) if expected == binding else Reply("Conflict")
                if job["payload"] is None:
                    return Reply("NotReady")
                binding = self._binding(job)
                if expected != binding:
                    return Reply("DigestMismatch")
                if not self._intact(job):
                    LOGGER.error("sealed output failed integrity verification")
                    return Reply("Unavailable")
                db.execute("UPDATE jobs SET state = 'Published' WHERE job_id = ?", (job_id,))
                return Reply("Published", binding)
        except sqlite3.Error:
            LOGGER.exception("publish storage operation failed")
            return Reply("OutcomeUnknown")

    def revoke(self, job_id: str, actor: str) -> Reply:
        try:
            if not self._may_write(job_id, actor, "revoker"):
                return Reply("Denied")
            with self._write() as db:
                job = self._job(db, job_id)
                if job is None or actor != job["revoker"]:
                    return Reply("Denied")
                if job["state"] == "Published":
                    return Reply("AlreadyPublished")
                if job["state"] == "Running":
                    db.execute("UPDATE jobs SET state = 'Revoked' WHERE job_id = ?", (job_id,))
                return Reply("Revoked")
        except sqlite3.Error:
            LOGGER.exception("revoke storage operation failed")
            return Reply("OutcomeUnknown")

    def _observe(self, job_id: str, actor: str, *, read: bool) -> Reply:
        try:
            with closing(self._connect()) as db:
                job = self._job(db, job_id)
                if job is None or actor != job["recipient"]:
                    return Reply("Denied")
                if not read:
                    return Reply(job["state"])
                if job["state"] != "Published":
                    return Reply("NotPublished")
                if not self._intact(job):
                    LOGGER.error("published output failed integrity verification")
                    return Reply("Unavailable")
                return Reply("Released", self._binding(job), job["payload"])
        except sqlite3.Error:
            LOGGER.exception("read or status storage operation failed")
            return Reply("Unavailable")

    def status(self, job_id: str, actor: str) -> Reply:
        return self._observe(job_id, actor, read=False)

    def read(self, job_id: str, actor: str) -> Reply:
        return self._observe(job_id, actor, read=True)
