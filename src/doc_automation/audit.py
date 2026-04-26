"""Append-only JSONL audit log — one entry per processed invoice."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from doc_automation.extraction.invoice import Invoice

logger = logging.getLogger(__name__)


class AuditLogger:
    """Thread-unsafe append-only audit logger backed by a JSONL file."""

    def __init__(self, log_path: Path) -> None:
        self._path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, entry: dict[str, Any]) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def log_invoice(
        self,
        invoice: Invoice,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        """
        status values: 'ok', 'blocked', 'quarantine', 'output_error'
        """
        entry: dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "status": status,
            "invoice_number": invoice.invoice_number,
            "vendor_name": invoice.vendor_name,
            "source_file": str(invoice.source_file.name),
            "template_used": invoice.template_used,
            "gl_code": invoice.gl_code,
            "total": str(invoice.total) if invoice.total is not None else None,
            "anomaly_flags": invoice.anomaly_flags,
        }
        if error:
            entry["error"] = error
        self._write(entry)
        logger.debug("audit: %s %s %s", status, invoice.vendor_name, invoice.invoice_number)

    def log_parse_error(self, source_file: Path, error: str) -> None:
        """Record a file that could not be parsed at all."""
        entry: dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "status": "parse_error",
            "source_file": str(source_file.name),
            "error": error,
        }
        self._write(entry)
        logger.debug("audit: parse_error %s", source_file.name)
