"""Shared pytest fixtures."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

SAMPLE_INVOICE_TEXT = """\
ACME Supplies Inc.
Invoice No: INV-2024-001
Invoice Date: January 15, 2024
Due Date: February 15, 2024

Description              Qty    Amount
Consulting Services      1      $1200.00
Travel Reimbursement     1      $300.00

Subtotal: $1500.00
Tax (10%): $150.00
Total: $1650.00
"""


@pytest.fixture
def config_dir() -> Path:
    """The project's actual config directory."""
    return CONFIG_DIR


@pytest.fixture
def invoices_dir() -> Path:
    return FIXTURES_DIR / "invoices"


@pytest.fixture
def text_invoice_pdf(tmp_path: Path) -> Path:
    """A real text-based PDF that pdfplumber can parse without OCR."""
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((50, 60), SAMPLE_INVOICE_TEXT, fontsize=11)
    path = tmp_path / "text_invoice.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def image_invoice_pdf(tmp_path: Path) -> Path:
    """
    An image-based PDF (text rendered as pixels) for testing the OCR path.
    pdfplumber will extract near-zero characters from it.
    """
    import fitz
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (612, 300), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), "Invoice No: INV-IMAGE-001", fill=(0, 0, 0))
    draw.text((50, 80), "Total: $999.00", fill=(0, 0, 0))
    png_path = tmp_path / "page.png"
    img.save(str(png_path))

    doc = fitz.open()
    page = doc.new_page(width=612, height=300)
    page.insert_image(fitz.Rect(0, 0, 612, 300), filename=str(png_path))
    path = tmp_path / "image_invoice.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def invoice_image_file(tmp_path: Path) -> Path:
    """A standalone PNG image with invoice-like text."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), "Invoice No: INV-PNG-001", fill=(0, 0, 0))
    draw.text((20, 50), "Total: $500.00", fill=(0, 0, 0))
    path = tmp_path / "invoice.png"
    img.save(str(path))
    return path
