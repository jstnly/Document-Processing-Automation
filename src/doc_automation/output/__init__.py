"""Output adapters — CSV, Excel, Google Sheets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from doc_automation.output.base import OutputAdapter
from doc_automation.output.csv_writer import CSVAdapter
from doc_automation.output.excel import ExcelAdapter
from doc_automation.output.sheets import GoogleSheetsAdapter


def build_adapter(output_cfg: dict[str, Any], columns: list[str]) -> OutputAdapter:
    """
    Instantiate the configured OutputAdapter from the parsed output.yaml dict.

    Raises ValueError for unknown adapter names.
    """
    adapter_name = output_cfg.get("adapter", "csv")

    if adapter_name == "csv":
        cfg = output_cfg.get("csv", {})
        return CSVAdapter(
            file_path=Path(cfg.get("file", "./output/invoices.csv")),
            columns=columns,
            append=bool(cfg.get("append", True)),
        )

    if adapter_name == "excel":
        cfg = output_cfg.get("excel", {})
        return ExcelAdapter(
            file_path=Path(cfg.get("file", "./output/invoices.xlsx")),
            columns=columns,
            sheet_name=cfg.get("sheet_name", "Invoices"),
            append=bool(cfg.get("append", True)),
        )

    if adapter_name == "google_sheets":
        cfg = output_cfg.get("google_sheets", {})
        return GoogleSheetsAdapter(
            spreadsheet_id=cfg["spreadsheet_id"],
            columns=columns,
            sheet_name=cfg.get("sheet_name", "Invoices"),
            credentials_env=cfg.get("credentials_env", "GOOGLE_SHEETS_SERVICE_ACCOUNT"),
        )

    raise ValueError(
        f"Unknown output adapter '{adapter_name}'. "
        "Valid choices: csv, excel, google_sheets"
    )


__all__ = [
    "OutputAdapter",
    "CSVAdapter",
    "ExcelAdapter",
    "GoogleSheetsAdapter",
    "build_adapter",
]
