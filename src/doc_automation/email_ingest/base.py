"""EmailSource ABC and EmailMessage dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class EmailMessage:
    uid: str
    subject: str
    sender: str
    received_at: datetime
    attachments: list[Path] = field(default_factory=list)


class EmailSource(ABC):
    """Fetch invoice emails from a mailbox."""

    @abstractmethod
    def fetch_new(self, working_dir: Path) -> list[EmailMessage]:
        """
        Download unprocessed emails and save attachments to working_dir.

        Returns one EmailMessage per qualifying email.
        Does NOT mark emails as processed — call mark_processed() after
        successful pipeline completion.
        """

    @abstractmethod
    def mark_processed(self, uid: str) -> None:
        """Mark a message as seen / move to processed folder."""

    def close(self) -> None:  # noqa: B027 — optional override, not abstract
        """Release the connection. Safe to call multiple times."""
