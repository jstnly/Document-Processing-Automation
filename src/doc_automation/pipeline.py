"""
Main pipeline orchestrator.

Flow per run:
  1. Drain outbox (retry previously-failed output writes)
  2. Fetch new emails (if email_source configured)
  3. For each attachment:
       parse → extract → COA match → anomaly check → output
  4. Quarantine blocked/unparseable files
  5. Return PipelineResult summary
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from doc_automation.audit import AuditLogger
from doc_automation.config import AnomalyRulesConfig, COARow, Config
from doc_automation.email_ingest.base import EmailSource
from doc_automation.extraction.extractor import extract_file
from doc_automation.extraction.invoice import Invoice
from doc_automation.outbox import Outbox
from doc_automation.output.base import OutputAdapter
from doc_automation.validation.anomaly import has_blocking_anomaly, run_anomaly_checks
from doc_automation.validation.coa import match_gl_code

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    processed: int = 0
    blocked: int = 0
    quarantined: int = 0
    errors: int = 0
    output_rows: int = 0
    outbox_retried: int = 0
    outbox_still_pending: int = 0

    def __str__(self) -> str:
        return (
            f"processed={self.processed} blocked={self.blocked} "
            f"quarantined={self.quarantined} errors={self.errors} "
            f"output_rows={self.output_rows}"
        )


class Pipeline:
    def __init__(
        self,
        config: Config,
        rules: AnomalyRulesConfig,
        coa: list[COARow],
        output_adapter: OutputAdapter,
        *,
        email_source: EmailSource | None = None,
        audit_logger: AuditLogger | None = None,
        outbox: Outbox | None = None,
        templates_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._rules = rules
        self._coa = coa
        self._output = output_adapter
        self._email = email_source
        self._audit = audit_logger
        self._outbox = outbox
        self._templates_dir = (
            templates_dir or Path("./config/templates")
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> PipelineResult:
        result = PipelineResult()
        working_dir = self._config.paths.working_dir
        working_dir.mkdir(parents=True, exist_ok=True)

        self._drain_outbox(result)
        self._ingest_emails(result, working_dir)
        return result

    def process_file(self, path: Path) -> Invoice:
        """Parse and enrich a single file; returns the Invoice (may have anomaly flags)."""
        invoice = self._extract(path, email_id=None)
        return invoice

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _drain_outbox(self, result: PipelineResult) -> None:
        if self._outbox is None:
            return
        due = self._outbox.drain()
        for entry_id, invoice in due:
            try:
                n = self._output.write_rows([invoice])
                self._outbox.mark_done(entry_id)
                result.outbox_retried += 1
                result.output_rows += n
                if self._audit:
                    self._audit.log_invoice(invoice, status="ok")
            except Exception as exc:
                self._outbox.reschedule(entry_id, str(exc))
                result.outbox_still_pending += 1
                logger.warning("outbox: retry failed for entry %d: %s", entry_id, exc)

    def _ingest_emails(self, result: PipelineResult, working_dir: Path) -> None:
        if self._email is None:
            return
        try:
            messages = self._email.fetch_new(working_dir)
        except Exception as exc:
            logger.error("email: fetch failed: %s", exc)
            result.errors += 1
            return

        for msg in messages:
            success = True
            for attachment in msg.attachments:
                inv = self._process_attachment(
                    attachment, email_id=msg.uid, result=result
                )
                if inv is None:
                    success = False

            if success:
                try:
                    self._email.mark_processed(msg.uid)
                except Exception as exc:
                    logger.warning("email: mark_processed failed for %s: %s", msg.uid, exc)

    def _process_attachment(
        self,
        path: Path,
        email_id: str | None,
        result: PipelineResult,
    ) -> Invoice | None:
        invoice = self._safe_extract(path, email_id, result)
        if invoice is None:
            return None

        invoice.processed_at = datetime.now(tz=timezone.utc)
        result.processed += 1

        if has_blocking_anomaly(invoice.anomaly_flags, self._rules):
            self._quarantine(path, invoice, result)
            return None

        try:
            n = self._output.write_rows([invoice])
            result.output_rows += n
            if self._audit:
                self._audit.log_invoice(invoice, status="ok")
        except Exception as exc:
            logger.error("output: write failed for %s: %s", path.name, exc)
            result.errors += 1
            if self._outbox:
                self._outbox.put(invoice, str(exc))
            if self._audit:
                self._audit.log_invoice(invoice, status="output_error", error=str(exc))

        return invoice

    def _safe_extract(
        self, path: Path, email_id: str | None, result: PipelineResult
    ) -> Invoice | None:
        try:
            invoice = extract_file(path, self._templates_dir)
        except Exception as exc:
            logger.error("parse: failed for %s: %s", path.name, exc)
            result.errors += 1
            if self._audit:
                self._audit.log_parse_error(path, str(exc))
            self._move_to_quarantine(path)
            result.quarantined += 1
            return None

        invoice.source_email_id = email_id
        invoice.attachment_sha256 = _sha256(path)
        match_gl_code(invoice, self._coa)
        new_flags = run_anomaly_checks(invoice, self._rules, self._config.defaults)
        invoice.anomaly_flags.extend(f for f in new_flags if f not in invoice.anomaly_flags)
        return invoice

    def _quarantine(
        self, path: Path, invoice: Invoice, result: PipelineResult
    ) -> None:
        self._move_to_quarantine(path)
        result.blocked += 1
        result.quarantined += 1
        if self._audit:
            self._audit.log_invoice(invoice, status="blocked")
        logger.info(
            "quarantine: %s blocked by %s",
            path.name,
            invoice.anomaly_flags,
        )

    def _move_to_quarantine(self, path: Path) -> None:
        quarantine = self._config.paths.quarantine_dir
        quarantine.mkdir(parents=True, exist_ok=True)
        dest = quarantine / path.name
        if dest.exists():
            dest = quarantine / f"{path.stem}_{path.stat().st_mtime_ns}{path.suffix}"
        try:
            shutil.move(str(path), str(dest))
        except Exception as exc:
            logger.warning("quarantine: could not move %s: %s", path.name, exc)

    def _extract(self, path: Path, email_id: str | None) -> Invoice:
        invoice = extract_file(path, self._templates_dir)
        invoice.source_email_id = email_id
        invoice.attachment_sha256 = _sha256(path)
        invoice.processed_at = datetime.now(tz=timezone.utc)
        match_gl_code(invoice, self._coa)
        new_flags = run_anomaly_checks(invoice, self._rules, self._config.defaults)
        invoice.anomaly_flags.extend(f for f in new_flags if f not in invoice.anomaly_flags)
        return invoice


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
