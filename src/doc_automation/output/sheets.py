"""
Google Sheets output adapter.

Requires a service-account JSON key and the spreadsheet shared with that account.
Set GOOGLE_SHEETS_SERVICE_ACCOUNT in .env to the path of the key file.
"""

from __future__ import annotations

import logging
import os

from doc_automation.extraction.invoice import Invoice
from doc_automation.output.base import OutputAdapter

logger = logging.getLogger(__name__)


class GoogleSheetsAdapter(OutputAdapter):
    """Append rows to a Google Sheet."""

    def __init__(
        self,
        spreadsheet_id: str,
        columns: list[str],
        *,
        sheet_name: str = "Invoices",
        credentials_env: str = "GOOGLE_SHEETS_SERVICE_ACCOUNT",
    ) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.columns = columns
        self.sheet_name = sheet_name
        self.credentials_env = credentials_env
        self._client = None

    def _get_client(self) -> object:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = os.environ.get(self.credentials_env)
        if not creds_path:
            raise RuntimeError(
                f"Environment variable {self.credentials_env!r} is not set. "
                "Point it to a Google service-account JSON key file."
            )
        creds = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            creds_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return gspread.authorize(creds)

    def write_rows(self, invoices: list[Invoice]) -> int:
        client = self._get_client()
        spreadsheet = client.open_by_key(self.spreadsheet_id)  # type: ignore[attr-defined]
        try:
            ws = spreadsheet.worksheet(self.sheet_name)
        except Exception:
            ws = spreadsheet.add_worksheet(title=self.sheet_name, rows=1000, cols=20)
            ws.append_row(self.columns)

        for invoice in invoices:
            row_dict = invoice.to_dict()
            row = [row_dict.get(col, "") for col in self.columns]
            ws.append_row(row)

        logger.info(
            "Google Sheets: wrote %d rows to %s/%s",
            len(invoices), self.spreadsheet_id, self.sheet_name,
        )
        return len(invoices)
