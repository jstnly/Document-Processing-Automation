"""OutputAdapter ABC — all output adapters implement this interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from doc_automation.extraction.invoice import Invoice


class OutputAdapter(ABC):
    """Write processed invoices to a destination."""

    @abstractmethod
    def write_rows(self, invoices: list[Invoice]) -> int:
        """
        Write invoices to the destination.

        Returns the number of rows actually written.
        Raises on unrecoverable error (caller should catch and queue to outbox).
        """

    def close(self) -> None:
        """Release any open resources. Called by the pipeline after each run."""
