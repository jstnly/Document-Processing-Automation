"""
SQLite-backed outbox for invoices whose output write failed.

On the next run, the pipeline calls drain() to retry them before processing
new emails.  Exponential back-off: delay doubles on each failed attempt,
capped at 24 hours.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from doc_automation.extraction.invoice import Invoice, LineItem

logger = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS outbox (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    next_retry_at TEXT    NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    invoice_json  TEXT    NOT NULL,
    last_error    TEXT    NOT NULL DEFAULT ''
);
"""

_BASE_DELAY = timedelta(minutes=5)
_MAX_DELAY = timedelta(hours=24)


def _retry_delay(attempts: int) -> timedelta:
    seconds = _BASE_DELAY.total_seconds() * (2 ** attempts)
    return min(timedelta(seconds=seconds), _MAX_DELAY)


def _invoice_to_json(invoice: Invoice) -> str:
    d = asdict(invoice)
    d["source_file"] = str(d["source_file"])
    d["subtotal"] = str(d["subtotal"]) if d["subtotal"] is not None else None
    d["tax_amount"] = str(d["tax_amount"]) if d["tax_amount"] is not None else None
    d["total"] = str(d["total"]) if d["total"] is not None else None
    d["invoice_date"] = d["invoice_date"].isoformat() if d["invoice_date"] else None
    d["due_date"] = d["due_date"].isoformat() if d["due_date"] else None
    d["processed_at"] = d["processed_at"].isoformat() if d["processed_at"] else None
    for item in d["line_items"]:
        item["quantity"] = str(item["quantity"]) if item["quantity"] is not None else None
        item["unit_price"] = str(item["unit_price"]) if item["unit_price"] is not None else None
        item["amount"] = str(item["amount"]) if item["amount"] is not None else None
    return json.dumps(d)


def _json_to_invoice(raw: str) -> Invoice:
    from datetime import date
    d = json.loads(raw)
    d["source_file"] = Path(d["source_file"])
    d["subtotal"] = Decimal(d["subtotal"]) if d["subtotal"] else None
    d["tax_amount"] = Decimal(d["tax_amount"]) if d["tax_amount"] else None
    d["total"] = Decimal(d["total"]) if d["total"] else None
    d["invoice_date"] = date.fromisoformat(d["invoice_date"]) if d["invoice_date"] else None
    d["due_date"] = date.fromisoformat(d["due_date"]) if d["due_date"] else None
    d["processed_at"] = (
        datetime.fromisoformat(d["processed_at"]) if d["processed_at"] else None
    )
    d["line_items"] = [
        LineItem(
            description=item["description"],
            quantity=Decimal(item["quantity"]) if item["quantity"] else None,
            unit_price=Decimal(item["unit_price"]) if item["unit_price"] else None,
            amount=Decimal(item["amount"]) if item["amount"] else None,
        )
        for item in d.get("line_items", [])
    ]
    return Invoice(**d)


class Outbox:
    """Local retry queue for invoices whose output destination was unreachable."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(_CREATE_SQL)
        self._conn.commit()

    def put(self, invoice: Invoice, error: str) -> None:
        """Enqueue an invoice for retry."""
        now = datetime.now(tz=UTC)
        self._conn.execute(
            "INSERT INTO outbox (created_at, next_retry_at, invoice_json, last_error) "
            "VALUES (?, ?, ?, ?)",
            (now.isoformat(), now.isoformat(), _invoice_to_json(invoice), error),
        )
        self._conn.commit()
        logger.debug("outbox: queued %s", invoice.invoice_number)

    def drain(self) -> list[tuple[int, Invoice]]:
        """Return all entries due for retry (next_retry_at <= now)."""
        now = datetime.now(tz=UTC).isoformat()
        rows = self._conn.execute(
            "SELECT id, invoice_json FROM outbox WHERE next_retry_at <= ? ORDER BY id",
            (now,),
        ).fetchall()
        result: list[tuple[int, Invoice]] = []
        for row_id, raw in rows:
            try:
                result.append((row_id, _json_to_invoice(raw)))
            except Exception as exc:
                logger.error("outbox: failed to deserialise entry %d: %s", row_id, exc)
        return result

    def mark_done(self, entry_id: int) -> None:
        self._conn.execute("DELETE FROM outbox WHERE id = ?", (entry_id,))
        self._conn.commit()

    def reschedule(self, entry_id: int, last_error: str) -> None:
        """Increment attempt_count and push next_retry_at back with exponential backoff."""
        row = self._conn.execute(
            "SELECT attempt_count FROM outbox WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return
        attempts = row[0] + 1
        delay = _retry_delay(attempts)
        next_retry = datetime.now(tz=UTC) + delay
        self._conn.execute(
            "UPDATE outbox SET attempt_count = ?, next_retry_at = ?, last_error = ? "
            "WHERE id = ?",
            (attempts, next_retry.isoformat(), last_error, entry_id),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
        return int(row[0])
