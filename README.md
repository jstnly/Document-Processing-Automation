# Document Processing Automation

A deterministic, AI-free invoice processing pipeline. Invoices arrive by email; the system extracts key fields, validates against your chart of accounts, flags anomalies by rule, and writes clean rows to a spreadsheet (CSV, Excel, or Google Sheets).

**No LLMs. No cloud ML. Same input → same output, every run.**

---

## What it does

```
email (IMAP) → PDF/OCR parsing → field extraction → GL code matching → anomaly rules → spreadsheet
                                                                                           ↓
                                                                                       audit log
```

- Reads unprocessed invoices from your mailbox (IMAP, or drop PDFs locally)
- Extracts: vendor name, invoice number, date, line items, subtotal, tax, total
- Matches each invoice to a GL code from your chart of accounts
- Flags rule violations: duplicate invoices, over-threshold amounts, mismatched math, future dates, unknown vendors, and more
- Writes one row per invoice to CSV, Excel, or Google Sheets
- Writes a JSONL audit trail with every decision

---

## Prerequisites

1. **Python 3.11+**
   ```bash
   python --version   # must be 3.11 or newer
   ```

2. **Tesseract OCR** (needed only for scanned/image PDFs; skip if all your invoices are text PDFs)
   - **Windows**: download the installer from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki); add the install directory to `PATH`
   - **macOS**: `brew install tesseract`
   - **Ubuntu/Debian**: `sudo apt install tesseract-ocr`
   - Verify: `tesseract --version`

---

## Installation

```bash
git clone <repo-url>
cd document-processing-automation

# Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# Install the package and all dependencies
pip install -e ".[dev]"
```

---

## Configuration

### 1. Secrets (`.env`)

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```dotenv
# IMAP mailbox
MAILBOX_USER=invoices@yourcompany.com
MAILBOX_PASS=your_app_password

# Google Sheets (only if using Google Sheets output)
GOOGLE_SHEETS_SERVICE_ACCOUNT=/absolute/path/to/service-account-key.json
```

**Never commit `.env` to git.** It is already in `.gitignore`.

### 2. Main config (`config/config.yaml`)

```yaml
mailbox:
  adapter: imap
  host: imap.gmail.com      # or imap.mail.yahoo.com, outlook.office365.com, etc.
  port: 993
  inbox_folder: INBOX

defaults:
  currency: USD
  amount_threshold: 10000.0  # invoices above this are flagged
```

For Gmail: use an [App Password](https://support.google.com/accounts/answer/185833), not your regular password.

### 3. Chart of accounts (`config/chart_of_accounts.csv`)

Each row maps a vendor or keyword to a GL code:

```csv
gl_code,name,vendor_match,keyword_match,default_for_unmatched
6100,Office Supplies,ACME|Staples,,false
6200,Software & SaaS,,subscription|software|saas,false
9999,Uncategorised,,,true
```

- `vendor_match` — regex matched against the vendor name
- `keyword_match` — regex matched against line-item descriptions
- Exactly **one** row must have `default_for_unmatched=true`

### 4. Output (`config/output.yaml`)

**CSV (default):**
```yaml
adapter: csv
csv:
  file: ./output/invoices.csv
  append: true
```

**Excel:**
```yaml
adapter: excel
excel:
  file: ./output/invoices.xlsx
  sheet_name: Invoices
  append: true
```

**Google Sheets:**
```yaml
adapter: google_sheets
google_sheets:
  spreadsheet_id: "your-spreadsheet-id-from-the-url"
  sheet_name: Invoices
  credentials_env: GOOGLE_SHEETS_SERVICE_ACCOUNT
```

### 5. Validate your config

```bash
python -m doc_automation validate-config
# Config OK: 11 anomaly rules, 10 chart-of-accounts entries
```

---

## Running

### Process a single PDF

```bash
python -m doc_automation process-file samples/invoices/my_invoice.pdf
```

Output:
```
vendor:   ACME Supplies Inc.
invoice:  INV-2024-001
date:     2024-01-15
total:    1650.00
gl_code:  6100
flags:    none
template: acme-supplies
Written to output.
```

### Run the full email pipeline

```bash
python -m doc_automation run
# Run complete: processed=12 blocked=1 quarantined=1 errors=0 output_rows=11
```

The pipeline will:
1. Retry any invoices previously stuck in the outbox
2. Fetch new UNSEEN emails from your inbox
3. Download PDF/image attachments and process each one
4. Write clean rows to your configured output
5. Mark processed emails as seen

### Re-process quarantined files

```bash
python -m doc_automation replay-quarantine
```

### List vendor templates

```bash
python -m doc_automation list-templates
```

---

## Adding a vendor template

Create `config/templates/<vendor-slug>.yaml` (see `config/templates/_default.yaml` as a reference):

```yaml
match: 'Acme\s+Supplies'   # regex matched against full document text
priority: 10               # higher = checked before lower-priority templates

fields:
  vendor_name:
    strategy: regex
    pattern: '^(Acme Supplies Inc\.?)\s*$'
    flags: MULTILINE

  invoice_number:
    strategy: regex
    pattern: 'Invoice\s+No[:\s]+(\S+)'
    flags: IGNORECASE

  total:
    strategy: regex
    pattern: '(?<!Sub)Total:\s*\$?([\d,]+\.\d{2})'
    flags: IGNORECASE
```

Then test it:
```bash
python -m doc_automation process-file samples/invoices/acme_invoice.pdf
```

---

## Audit log

Every processed invoice produces a line in `logs/audit.jsonl`:

```json
{"ts": "2024-01-15T14:32:01+00:00", "status": "ok", "invoice_number": "INV-001",
 "vendor_name": "ACME Supplies Inc.", "gl_code": "6100", "total": "1650.00",
 "anomaly_flags": [], "template_used": "acme-supplies", "source_file": "invoice.pdf"}
```

Statuses: `ok`, `blocked` (blocking anomaly), `quarantine` (parse failure), `output_error`.

To view recent entries:
```bash
# Last 10 entries:
tail -10 logs/audit.jsonl | python -m json.tool

# All blocked invoices:
grep '"status": "blocked"' logs/audit.jsonl
```

---

## Troubleshooting

| Problem | Check |
|---|---|
| `Config errors found` on startup | Run `validate-config` and fix all listed issues |
| Invoice not extracted | Check `logs/audit.jsonl` for `parse_error`; try adding a vendor template |
| Wrong GL code assigned | Edit `config/chart_of_accounts.csv`; run `validate-config` to verify |
| Invoice flagged incorrectly | Edit `config/anomaly_rules.yaml` — adjust params or lower severity to `info` |
| Attachment not downloaded | Check IMAP credentials in `.env`; verify `attachment_types` in `config.yaml` |
| File in quarantine | Check audit log for the reason; fix the template, then `replay-quarantine` |
| Google Sheets auth error | Ensure service account JSON path is correct in `.env` and the sheet is shared with the service account email |

---

## Project structure

```
config/
  config.yaml              # main config (mailbox, defaults, paths)
  anomaly_rules.yaml       # 11 named anomaly rules with severity
  chart_of_accounts.csv    # GL code to vendor/keyword mapping
  output.yaml              # output adapter selection + settings
  templates/
    _default.yaml          # fallback extraction template
    acme-supplies.yaml     # vendor-specific override (example)
src/doc_automation/
  pipeline.py              # main orchestrator
  audit.py                 # JSONL audit log
  outbox.py                # SQLite retry queue
  cli.py                   # argparse CLI
  config.py                # pydantic config models
  email_ingest/            # IMAP adapter (Gmail/Outlook stubs)
  parsing/                 # PDF (pdfplumber) + OCR (Tesseract/PyMuPDF)
  extraction/              # template engine + strategies + Invoice dataclass
  validation/              # COA matching + 11 anomaly rules
  output/                  # CSV / Excel / Google Sheets adapters
tests/                     # 260 tests, 95% coverage
logs/                      # audit.jsonl (created at runtime, gitignored)
working/                   # temporary attachment downloads (gitignored)
quarantine/                # failed invoices (gitignored)
output/                    # generated spreadsheets (gitignored)
```

---

## Development

```bash
# Run all tests:
pytest

# With coverage:
pytest --cov=doc_automation --cov-report=term-missing

# Lint:
ruff check src/ tests/

# Type-check:
mypy src/
```

See `CLAUDE.md` for AI-session continuation instructions and architectural decisions.
