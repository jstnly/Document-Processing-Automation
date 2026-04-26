"""Microsoft Graph / Outlook email source — future implementation."""

from __future__ import annotations

from pathlib import Path

from doc_automation.email_ingest.base import EmailMessage, EmailSource


class OutlookSource(EmailSource):
    """Fetch invoices via Microsoft Graph API (app-only auth)."""

    def fetch_new(self, working_dir: Path) -> list[EmailMessage]:
        raise NotImplementedError(
            "Outlook adapter is not yet implemented. "
            "Use adapter: imap with Outlook's IMAP settings as a workaround."
        )

    def mark_processed(self, uid: str) -> None:
        raise NotImplementedError
