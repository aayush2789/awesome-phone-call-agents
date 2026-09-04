"""CloseLoop Persistent Idempotency Ledger (SQLite Implementation).

Provides a database-enforced, crash-safe execution ledger. Enforces idempotency
via a UNIQUE constraint on sha256(run_id:rung:attempt) and supports transactional
state transitions (planned -> executing -> terminal) with crash recovery.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from closeloop.models import (
    AuditEntry,
    CallAttempt,
    CallPlan,
    CallRun,
    OutcomeResult,
    WorkflowResult,
    WorkflowSpec,
    compute_idempotency_key,
)
from closeloop.safety import SafetyViolationError


class DuplicateAttemptError(SafetyViolationError):
    """Raised when an attempt with the same idempotency key already exists in the ledger."""


class LedgerRepositoryBase(ABC):
    """Abstract interface for CloseLoop persistent ledger storage engines."""

    @abstractmethod
    def record_workflow(self, spec: WorkflowSpec) -> None:
        """Register a new workflow specification."""

    @abstractmethod
    def get_workflow_spec(self, run_id: str) -> Optional[WorkflowSpec]:
        """Retrieve a stored workflow specification."""

    @abstractmethod
    def record_attempt(self, attempt: CallAttempt) -> None:
        """Persist a newly planned call attempt enforcing idempotency."""

    @abstractmethod
    def update_attempt_status(
        self,
        idempotency_key: str,
        status: str,
        run: Optional[CallRun] = None,
        outcome: Optional[OutcomeResult] = None,
        error: Optional[str] = None,
    ) -> None:
        """Atomically transition an attempt's execution status."""

    @abstractmethod
    def get_attempt(self, idempotency_key: str) -> Optional[CallAttempt]:
        """Fetch an attempt by its deterministic idempotency key."""

    @abstractmethod
    def list_attempts(self, run_id: str) -> list[CallAttempt]:
        """List all attempts registered for a specific workflow run."""

    @abstractmethod
    def is_attempt_terminal(self, run_id: str, rung: str, attempt_num: int) -> bool:
        """Check if an attempt has reached a terminal state."""

    @abstractmethod
    def has_attempt(self, run_id: str, rung: str, attempt_num: int) -> bool:
        """Check whether an attempt already exists in any state."""

    @abstractmethod
    def record_audit(self, run_id: str, entry: AuditEntry) -> None:
        """Persist an audit entry."""

    @abstractmethod
    def get_audit_trail(self, run_id: str) -> list[AuditEntry]:
        """Retrieve all audit entries for a workflow run."""

    @abstractmethod
    def reconcile_in_flight_attempts(self, run_id: str) -> list[CallAttempt]:
        """Reconcile attempts left in 'executing' state after a crash."""

    @abstractmethod
    def save_workflow_result(self, result: WorkflowResult) -> None:
        """Persist final workflow outcome envelope."""

    @abstractmethod
    def get_workflow_result(self, run_id: str) -> Optional[WorkflowResult]:
        """Retrieve final workflow outcome envelope."""


class SQLiteLedger(LedgerRepositoryBase):
    """Crash-safe SQLite implementation of the CloseLoop execution ledger."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Enable foreign keys and WAL mode for crash safety
        self._conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL;")

        self._create_tables()

    def _create_tables(self) -> None:
        """Initialize database schema with strict idempotency constraints."""
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS call_attempts (
                    idempotency_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    rung TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    phone TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT,
                    run_json TEXT,
                    outcome_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES workflows(run_id) ON DELETE CASCADE,
                    UNIQUE(run_id, rung, attempt)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    rung TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    outcome_class TEXT,
                    details_json TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES workflows(run_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_attempts_run_id ON call_attempts(run_id);
                CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_log(run_id);
                """
            )

    def record_workflow(self, spec: WorkflowSpec) -> None:
        """Register workflow specification in the ledger."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO workflows (run_id, status, spec_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    spec.run_id,
                    "active",
                    json.dumps(spec.model_dump(mode="json")),
                    now_str,
                    now_str,
                ),
            )

    def get_workflow_spec(self, run_id: str) -> Optional[WorkflowSpec]:
        """Fetch workflow spec by run_id."""
        cur = self._conn.execute("SELECT spec_json FROM workflows WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        if not row:
            return None
        return WorkflowSpec(**json.loads(row["spec_json"]))

    def record_attempt(self, attempt: CallAttempt) -> None:
        """Persist a call attempt.

        Raises DuplicateAttemptError if an attempt with this key already exists.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        plan_str = json.dumps(attempt.plan.model_dump(mode="json")) if attempt.plan else None
        run_str = json.dumps(attempt.run.model_dump(mode="json")) if attempt.run else None
        outcome_str = json.dumps(attempt.outcome.model_dump(mode="json")) if attempt.outcome else None

        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO call_attempts (
                        idempotency_key, run_id, rung, attempt, phone, status,
                        plan_json, run_json, outcome_json, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.idempotency_key,
                        attempt.run_id,
                        attempt.rung,
                        attempt.attempt,
                        attempt.phone,
                        attempt.status,
                        plan_str,
                        run_str,
                        outcome_str,
                        attempt.error,
                        now_str,
                        now_str,
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DuplicateAttemptError(
                f"Attempt with idempotency key '{attempt.idempotency_key}' (rung '{attempt.rung}', attempt {attempt.attempt}) already exists in the ledger."
            ) from err

    def update_attempt_status(
        self,
        idempotency_key: str,
        status: str,
        run: Optional[CallRun] = None,
        outcome: Optional[OutcomeResult] = None,
        error: Optional[str] = None,
    ) -> None:
        """Atomically transition attempt status."""
        now_str = datetime.now(timezone.utc).isoformat()
        updates = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now_str]

        if run is not None:
            updates.append("run_json = ?")
            params.append(json.dumps(run.model_dump(mode="json")))
        if outcome is not None:
            updates.append("outcome_json = ?")
            params.append(json.dumps(outcome.model_dump(mode="json")))
        if error is not None:
            updates.append("error = ?")
            params.append(error)

        params.append(idempotency_key)
        sql = f"UPDATE call_attempts SET {', '.join(updates)} WHERE idempotency_key = ?"

        with self._conn:
            self._conn.execute(sql, params)

    def get_attempt(self, idempotency_key: str) -> Optional[CallAttempt]:
        """Fetch attempt by idempotency key."""
        cur = self._conn.execute("SELECT * FROM call_attempts WHERE idempotency_key = ?", (idempotency_key,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_attempt(row)

    def list_attempts(self, run_id: str) -> list[CallAttempt]:
        """List all attempts for a given run_id ordered by creation time."""
        cur = self._conn.execute(
            "SELECT * FROM call_attempts WHERE run_id = ? ORDER BY attempt ASC",
            (run_id,),
        )
        return [self._row_to_attempt(row) for row in cur.fetchall()]

    def is_attempt_terminal(self, run_id: str, rung: str, attempt_num: int) -> bool:
        """Check if specified rung attempt has reached terminal status."""
        key = compute_idempotency_key(run_id, rung, attempt_num)
        cur = self._conn.execute("SELECT status FROM call_attempts WHERE idempotency_key = ?", (key,))
        row = cur.fetchone()
        return bool(row and row["status"] == "terminal")

    def has_attempt(self, run_id: str, rung: str, attempt_num: int) -> bool:
        """Check if an attempt exists under this key in any status."""
        key = compute_idempotency_key(run_id, rung, attempt_num)
        cur = self._conn.execute("SELECT 1 FROM call_attempts WHERE idempotency_key = ?", (key,))
        return cur.fetchone() is not None

    def record_audit(self, run_id: str, entry: AuditEntry) -> None:
        """Persist structured audit record."""
        details_str = json.dumps(entry.details) if entry.details else None
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO audit_log (run_id, rung, attempt, action, outcome_class, details_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    entry.rung,
                    entry.attempt,
                    entry.action,
                    entry.outcome_class,
                    details_str,
                    entry.timestamp.isoformat(),
                ),
            )

    def get_audit_trail(self, run_id: str) -> list[AuditEntry]:
        """Retrieve audit log for workflow."""
        cur = self._conn.execute("SELECT * FROM audit_log WHERE run_id = ? ORDER BY id ASC", (run_id,))
        entries: list[AuditEntry] = []
        for row in cur.fetchall():
            details = json.loads(row["details_json"]) if row["details_json"] else {}
            entries.append(
                AuditEntry(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    rung=row["rung"],
                    attempt=row["attempt"],
                    action=row["action"],
                    outcome_class=row["outcome_class"],
                    details=details,
                )
            )
        return entries

    def reconcile_in_flight_attempts(self, run_id: str) -> list[CallAttempt]:
        """Crash Recovery: Reconcile attempts left in 'executing' status upon restart.

        Marks orphaned 'executing' attempts as 'needs_reconciliation' so they
        are not blindly redialed, preserving the zero-duplicate-call invariant.
        """
        cur = self._conn.execute(
            "SELECT * FROM call_attempts WHERE run_id = ? AND status = 'executing'",
            (run_id,),
        )
        stranded = [self._row_to_attempt(row) for row in cur.fetchall()]

        if stranded:
            now_str = datetime.now(timezone.utc).isoformat()
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE call_attempts
                    SET status = 'needs_reconciliation', updated_at = ?
                    WHERE run_id = ? AND status = 'executing'
                    """,
                    (now_str, run_id),
                )

        return stranded

    def save_workflow_result(self, result: WorkflowResult) -> None:
        """Persist finalized workflow outcome."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                UPDATE workflows
                SET status = ?, result_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (result.status, json.dumps(result.model_dump(mode="json")), now_str, result.run_id),
            )

    def get_workflow_result(self, run_id: str) -> Optional[WorkflowResult]:
        """Fetch finalized workflow result envelope."""
        cur = self._conn.execute("SELECT result_json FROM workflows WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        if not row or not row["result_json"]:
            return None
        data = json.loads(row["result_json"])
        return WorkflowResult(**data)

    def _row_to_attempt(self, row: sqlite3.Row) -> CallAttempt:
        """Deserialize database row into a CallAttempt model."""
        plan = CallPlan(**json.loads(row["plan_json"])) if row["plan_json"] else None
        run = CallRun(**json.loads(row["run_json"])) if row["run_json"] else None
        outcome = OutcomeResult(**json.loads(row["outcome_json"])) if row["outcome_json"] else None

        return CallAttempt(
            idempotency_key=row["idempotency_key"],
            run_id=row["run_id"],
            rung=row["rung"],
            attempt=row["attempt"],
            phone=row["phone"],
            status=row["status"],
            plan=plan,
            run=run,
            outcome=outcome,
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def close(self) -> None:
        """Close SQLite database connection."""
        self._conn.close()
