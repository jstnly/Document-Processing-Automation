"""
SQLite-backed deduplication store for processed invoices.

Tracks (vendor_id, invoice_number, processed_at) so the anomaly engine can
detect duplicate invoice submissions within a configurable lookback window.

Separate from the JSONL audit log (which is human-readable and append-only)
and from the outbox (which handles output retries).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from doc_automation.extraction.invoice import Invoice

logger = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS seen_invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id     TEXT    NOT NULL,
    invoice_number TEXT   NOT NULL,
    processed_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_lookup
    ON seen_invoices (vendor_id, invoice_number, processed_at);
"""


class DeduplicateDB:
    """
    Write-once store for successfully processed (vendor_id, invoice_number) pairs.

    The pipeline calls record() after a successful output write.
    The anomaly engine calls is_duplicate() before writing.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        for stmt in _CREATE_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()
        self._path = db_path

    @property
    def path(self) -> Path:
        return self._path

    def is_duplicate(
        self, vendor_id: str, invoice_number: str, *, days: int = 365
    ) -> bool:
        """
        Return True if this (vendor_id, invoice_number) pair was processed
        within the last `days` days.
        """
        row = self._conn.execute(
            """
            SELECT 1 FROM seen_invoices
            WHERE vendor_id = ? AND invoice_number = ?
              AND processed_at > datetime('now', ?)
            LIMIT 1
            """,
            (vendor_id, invoice_number, f"-{days} days"),
        ).fetchone()
        return row is not None

    def record(self, invoice: Invoice) -> None:
        """Record a successfully processed invoice. No-op if fields are missing."""
        if not invoice.vendor_id or not invoice.invoice_number:
            return
        ts = (
            invoice.processed_at.isoformat()
            if invoice.processed_at
            else datetime.now(tz=UTC).isoformat()
        )
        self._conn.execute(
            "INSERT INTO seen_invoices (vendor_id, invoice_number, processed_at) "
            "VALUES (?, ?, ?)",
            (invoice.vendor_id, invoice.invoice_number, ts),
        )
        self._conn.commit()
        logger.debug(
            "dedup: recorded %s / %s", invoice.vendor_id, invoice.invoice_number
        )

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
