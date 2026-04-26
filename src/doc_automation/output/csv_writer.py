"""CSV output adapter."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from doc_automation.extraction.invoice import Invoice
from doc_automation.output.base import OutputAdapter

logger = logging.getLogger(__name__)


class CSVAdapter(OutputAdapter):
    """Append (or create) a CSV file with one row per invoice."""

    def __init__(
        self,
        file_path: Path,
        columns: list[str],
        *,
        append: bool = True,
    ) -> None:
        self.file_path = file_path
        self.columns = columns
        self.append = append
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    def write_rows(self, invoices: list[Invoice]) -> int:
        write_header = not self.append or not self.file_path.exists()
        mode = "a" if self.append else "w"

        with open(self.file_path, mode=mode, newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=self.columns, extrasaction="ignore"
            )
            if write_header:
                writer.writeheader()
            for invoice in invoices:
                writer.writerow(invoice.to_dict())

        logger.info("CSV: wrote %d rows to %s", len(invoices), self.file_path)
        return len(invoices)
