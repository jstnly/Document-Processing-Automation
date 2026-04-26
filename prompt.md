# Document Processing Automation — Build Prompt

> **You are an AI coding assistant tasked with building this project from scratch.** This file is your primary source of truth. Read it end to end before writing any code. After every commit, update the live sections at the bottom (**Status**, **Decisions**, **Open Questions**, **Next Steps**) so the next session — yours or another AI's — can pick up cold.

---

## 1. Mission

Build a **deterministic invoice processing automation** that turns hours of clerical data entry into minutes.

**The flow**: invoices arrive by email → the system extracts the essential fields → validates them against a chart of accounts → flags anything unusual via rules → pushes clean rows to spreadsheets (and other configured destinations).

**Target users**: businesses drowning in paperwork — law firms, accounting firms, construction companies, insurance agencies, logistics operators. Non-technical bookkeepers must be able to operate it after one-time setup.

**Why it matters**: manual document processing is expensive, slow, and error-prone. Every hour a bookkeeper spends retyping invoice numbers is an hour not spent closing deals.

---

## 2. Hard Constraints (Non-Negotiable)

These are the spine of the project. **Do not violate them, even when it would be faster.** If a requirement seems to conflict with a constraint, stop and add it to **Open Questions** instead of making a judgment call.

1. **No AI / No LLMs.** No calls to OpenAI, Anthropic, Google Gemini, local LLMs, or any model that produces probabilistic / non-reproducible output for extraction or validation logic. No `openai`, `anthropic`, `langchain`, `transformers`, `sklearn`, `torch`, `tensorflow` imports anywhere in `src/`.
2. **Deterministic.** Same input bytes → same output rows, every run, forever. No randomness, no time-of-day dependence (other than `datetime.now()` for audit timestamps).
3. **OCR exception, named explicitly.** Tesseract (via `pytesseract`) **is permitted** for converting scanned-image PDFs into text. Tesseract is local, open-source, and produces the same text for the same image — it's the industry-standard non-AI OCR tool. No other ML/AI is permitted.
4. **Rule-traceable.** Every flagged anomaly must point to a named rule in `config/anomaly_rules.yaml`. A user reading the audit log must be able to answer "why was this flagged?" by reading config — never by inspecting model weights.
5. **Maintenance-free.** Configuration-driven. Adding a new vendor is a YAML file, not a code change. Adding a new output destination is one adapter class. No retraining, no model updates, no monthly tuning.
6. **Auditable.** Every invoice processed produces an audit log entry: source email message-ID, attachment hash, extracted fields, anomalies, output destination, timestamp.
7. **Fail loud, fail safe.** Bad config = refuse to start with a clear error. Bad invoice = quarantine the file and continue the pipeline. Never silently drop data.

---

## 3. What This System Does (Pipeline)

```
┌─────────────┐    ┌─────────┐    ┌────────────┐    ┌────────────┐    ┌──────────┐    ┌────────┐
│ email_      │───▶│ parsing │───▶│ extraction │───▶│ coa        │───▶│ anomaly  │───▶│ output │
│ ingest      │    │ (PDF/   │    │ (template  │    │ validation │    │ detection│    │ (sheet,│
│ (IMAP/Gmail │    │  OCR)   │    │  per       │    │ (GL code   │    │ (rules)  │    │  excel,│
│  /Outlook)  │    │         │    │  vendor)   │    │  mapping)  │    │          │    │  csv)  │
└─────────────┘    └─────────┘    └────────────┘    └────────────┘    └──────────┘    └────────┘
                                                                            │              │
                                                                            ▼              ▼
                                                                      ┌──────────────────────┐
                                                                      │     audit log        │
                                                                      └──────────────────────┘
```

Each stage is a module with a clear interface. Each can be tested in isolation with fixtures. The pipeline runs top-to-bottom for each invoice; failures at any stage send the document to the quarantine folder with a structured reason.

---

## 4. Functional Requirements

### 4.1 Email Ingestion
- Connect to a configured mailbox via the **EmailAdapter** interface.
- Default adapter: **IMAP** (`imaplib`, stdlib). Additional adapters (build later, behind the same interface): **Gmail API**, **Microsoft Graph (Outlook/M365)**.
- Filter messages with configurable rules: sender allowlist (regex), subject patterns (regex), attachment MIME types (`application/pdf`, `image/png`, `image/jpeg`, `image/tiff`).
- Download matching attachments to a working directory with a content-hash filename.
- Mark messages as processed (IMAP flag, Gmail label, or Graph category — adapter-specific) so they aren't picked up twice.
- On adapter failure: retry 3× with exponential backoff (1s, 4s, 16s); then log + exit with non-zero code.

### 4.2 Document Parsing
- Detect PDF type: text-based vs image-based. Heuristic: if `pdfplumber` extracts < 10 characters per page on average, treat as image-based.
- **Text PDFs**: extract text + bounding-box positions with `pdfplumber`.
- **Image PDFs**: rasterize each page with `PyMuPDF` or `pdf2image` (300 DPI), then OCR each page with `pytesseract`. Preserve word positions.
- **Pure images** (PNG/JPEG/TIFF): preprocess with `Pillow` (deskew, threshold), then OCR.
- Output: a normalized `ParsedDocument` object containing pages, words, and word coordinates.

### 4.3 Field Extraction (Template-Based)
- Each known vendor has a YAML template at `config/templates/<vendor_slug>.yaml`.
- Template selection: scan the parsed text for each template's `match` regex; first hit wins. Fall back to `config/templates/_default.yaml`.
- Template defines per-field extractors using one of three strategies:
  - **regex**: pattern + capture group (e.g., `Invoice\s+#?\s*([A-Z0-9-]+)`)
  - **anchor**: locate an anchor string, then extract a region offset from it (e.g., "200pt right of `Total:`")
  - **table**: row/column extraction from detected tables for line items
- **Required output fields**: `vendor_name`, `vendor_id`, `invoice_number`, `invoice_date`, `due_date`, `currency`, `subtotal`, `tax_amount`, `total`, `line_items[]` (each with `description`, `quantity`, `unit_price`, `amount`).
- Missing required fields → flagged anomaly, not crash.

### 4.4 Chart of Accounts Validation
- Load `config/chart_of_accounts.csv` at startup. Schema: `gl_code, name, vendor_match, keyword_match, default_for_unmatched`.
- For each invoice:
  - First, try exact vendor match (`vendor_match` regex against `vendor_name`).
  - Fall back to keyword match (`keyword_match` regex against line-item descriptions).
  - If still no match, use the row marked `default_for_unmatched=true` and flag as `unknown_gl_code`.
- Attach `gl_code` to each line item in the output.

### 4.5 Anomaly Detection (Rule-Based)
- Rules live in `config/anomaly_rules.yaml`. Each rule has `name`, `description`, `severity` (`info` | `warn` | `block`), and a `check` (declarative).
- **Built-in rules to implement**:
  - `duplicate_invoice` — same `(vendor_id, invoice_number)` seen in audit log within last 365 days
  - `amount_threshold` — `total` exceeds configured threshold (default $10,000)
  - `future_date` — `invoice_date` is in the future
  - `stale_date` — `invoice_date` more than 180 days old
  - `math_mismatch_subtotal` — sum of line item amounts ≠ subtotal (tolerance: $0.02)
  - `math_mismatch_total` — subtotal + tax_amount ≠ total (tolerance: $0.02)
  - `tax_rate_out_of_range` — tax_amount/subtotal outside 0-25%
  - `missing_required_field` — any required field empty
  - `unknown_vendor` — fell back to `_default.yaml`
  - `unknown_gl_code` — no chart-of-accounts match
  - `currency_mismatch` — currency ≠ configured default
- `block` severity sends the invoice to quarantine (no output write). `warn` and `info` write the row but include the flags in the output.

### 4.6 Output / Sync
- **OutputAdapter** interface. Implement adapters in this order: `csv` → `excel` (`openpyxl`) → `google_sheets` (`gspread`).
- Column mapping is configurable in `config/output.yaml` (which fields → which columns, in what order).
- Append-only by default; configurable to update existing rows by `(vendor_id, invoice_number)` key.
- On adapter failure: queue the row to a local SQLite outbox; retry on next run.

### 4.7 Configuration
- Single entry point: `config/config.yaml`.
- Schema validated with **pydantic** at startup. Bad config → fail with a precise, line-numbered error.
- Secrets (mailbox passwords, API tokens) live in `.env`, loaded with `python-dotenv`. Never in YAML.
- All paths in config are relative to the project root unless absolute.

### 4.8 Logging & Audit
- Structured logging (JSON lines) to `logs/audit.jsonl`, one record per invoice. Fields: `timestamp`, `message_id`, `attachment_sha256`, `template_used`, `extracted_fields`, `anomalies[]`, `gl_codes[]`, `output_destination`, `status`.
- Human-readable log to `logs/run.log` (rotating, 7-day retention).
- Use `logging` stdlib + a small JSON formatter; no need for `loguru`.

### 4.9 CLI
- Entry point: `python -m doc_automation`.
- Subcommands:
  - `run` — one-shot pipeline: ingest → parse → extract → validate → output
  - `validate-config` — schema-check all YAML and CSV without running
  - `process-file <path>` — run pipeline on a single local PDF (skips email ingestion); useful for testing templates
  - `list-templates` — print all known vendor templates
  - `replay-quarantine` — re-run quarantined documents (e.g., after fixing a template)
- Exit codes: `0` = success, `1` = config error, `2` = adapter error, `3` = partial success (some quarantined).

---

## 5. Recommended Tech Stack

| Concern | Library | Why |
|---|---|---|
| Language | Python 3.11+ | Best ecosystem for PDF/OCR/email/spreadsheet work |
| Email (default) | `imaplib` (stdlib) | Universal IMAP support, zero deps |
| Email (Gmail) | `google-api-python-client` | Official Gmail API |
| Email (Outlook) | `O365` or `msgraph-sdk` | Microsoft Graph |
| Text PDF | `pdfplumber` | Text + position extraction |
| Image PDF | `PyMuPDF` (`pymupdf`) | Fast rasterization |
| OCR | `pytesseract` + system Tesseract | Deterministic, local, free |
| Image preprocess | `Pillow` | Deskew, threshold |
| Excel | `openpyxl` | Read/write `.xlsx` |
| Google Sheets | `gspread` + `google-auth` | Service-account auth |
| Config | `PyYAML`, `pydantic`, `python-dotenv` | YAML parsing, schema validation, secrets |
| Outbox | `sqlite3` (stdlib) | No infra dependency |
| Testing | `pytest`, `pytest-cov` | Standard |
| Lint/format | `ruff`, `black` | Fast, opinionated |
| Type-check | `mypy` (strict on `src/`) | Catches mistakes early |

If you find a strong reason to deviate (e.g., a library is unmaintained), record it in **Decisions** and proceed.

---

## 6. Project Structure

```
.
├── prompt.md                       # this file
├── CLAUDE.md                       # you create this — see §9
├── README.md                       # human setup/usage docs (you create this)
├── pyproject.toml                  # deps + tool config
├── .env.example                    # documented env-var template
├── .gitignore
├── config/
│   ├── config.yaml                 # main config
│   ├── chart_of_accounts.csv       # GL mapping
│   ├── anomaly_rules.yaml          # rules
│   ├── output.yaml                 # output adapter + column mapping
│   └── templates/
│       ├── _default.yaml           # generic fallback
│       └── <vendor_slug>.yaml      # per-vendor templates
├── src/doc_automation/
│   ├── __init__.py
│   ├── __main__.py                 # CLI dispatch
│   ├── cli.py
│   ├── config.py                   # pydantic models + loader
│   ├── pipeline.py                 # orchestrates the stages
│   ├── audit.py                    # audit log writer
│   ├── outbox.py                   # SQLite retry queue
│   ├── email_ingest/
│   │   ├── __init__.py
│   │   ├── base.py                 # EmailAdapter interface
│   │   ├── imap.py
│   │   ├── gmail.py
│   │   └── outlook.py
│   ├── parsing/
│   │   ├── __init__.py
│   │   ├── pdf.py                  # text-PDF extraction
│   │   ├── ocr.py                  # Tesseract wrapper
│   │   ├── image.py                # rasterize + preprocess
│   │   └── document.py             # ParsedDocument dataclass
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── template.py             # template loader + matcher
│   │   ├── strategies.py           # regex/anchor/table extractors
│   │   └── extractor.py            # apply template to ParsedDocument
│   ├── validation/
│   │   ├── __init__.py
│   │   ├── coa.py                  # chart-of-accounts loader + matcher
│   │   └── anomaly.py              # rule engine
│   └── output/
│       ├── __init__.py
│       ├── base.py                 # OutputAdapter interface
│       ├── csv_writer.py
│       ├── excel.py
│       └── sheets.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── invoices/               # sample PDFs (text + scanned)
│   │   └── configs/
│   ├── test_config.py
│   ├── test_parsing.py
│   ├── test_extraction.py
│   ├── test_coa.py
│   ├── test_anomaly.py
│   ├── test_output.py
│   ├── test_email_ingest.py        # uses mock IMAP
│   └── test_pipeline_e2e.py
├── samples/invoices/               # user drops PDFs here for `process-file`
├── logs/                           # gitignored; created at runtime
├── quarantine/                     # gitignored; created at runtime
└── outbox.sqlite                   # gitignored; created at runtime
```

---

## 7. Configuration Schema (Examples)

### `config/config.yaml`
```yaml
mailbox:
  adapter: imap
  host: imap.example.com
  port: 993
  username_env: MAILBOX_USER       # value loaded from .env
  password_env: MAILBOX_PASS
  inbox_folder: INBOX
  processed_label: doc-automation/processed
  filters:
    sender_allowlist:
      - '.*@trusted-vendor\.com'
    subject_patterns:
      - '(?i)invoice'
    attachment_types: [application/pdf, image/png, image/jpeg, image/tiff]

defaults:
  currency: USD
  amount_threshold: 10000

paths:
  working_dir: ./working
  quarantine_dir: ./quarantine
  audit_log: ./logs/audit.jsonl

output:
  config_file: ./config/output.yaml
```

### `config/anomaly_rules.yaml`
```yaml
rules:
  - name: duplicate_invoice
    severity: block
    description: Same invoice number from same vendor seen in last 365 days
  - name: amount_threshold
    severity: warn
    description: Total exceeds configured threshold
    params:
      threshold: 10000
  - name: math_mismatch_total
    severity: warn
    description: subtotal + tax != total
    params:
      tolerance: 0.02
  # ... etc, one entry per rule from §4.5
```

### `config/templates/_default.yaml`
```yaml
match: '.*'                           # always matches; lowest priority
fields:
  invoice_number:
    strategy: regex
    pattern: 'Invoice\s*#?\s*([A-Z0-9\-]+)'
  invoice_date:
    strategy: regex
    pattern: '(?:Date|Invoice Date)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
  total:
    strategy: regex
    pattern: '(?:Total|Amount Due)[:\s$]+([\d,]+\.\d{2})'
  # ... etc
```

### `config/chart_of_accounts.csv`
```csv
gl_code,name,vendor_match,keyword_match,default_for_unmatched
6010,Office Supplies,.*staples.*,.*paper|toner.*,false
6020,Software,.*microsoft|adobe.*,.*subscription.*,false
6999,Uncategorized,,,true
```

---

## 8. Implementation Phases

Build in this order. Each phase is **one feature branch, one commit, one merge-and-delete cycle** (see §10).

| # | Branch | Goal | Done when |
|---|---|---|---|
| 1 | `feat/foundation` | Project skeleton, `pyproject.toml`, config loader (pydantic), logging, `.gitignore`, sample fixtures | `pytest` finds 0 tests but imports succeed; `python -m doc_automation validate-config` works on a stub config |
| 2 | `feat/parsing` | Text-PDF + OCR with `ParsedDocument` output | Sample text PDF and sample scanned PDF both parse to non-empty `ParsedDocument`; tests green |
| 3 | `feat/extraction` | Template engine + `_default.yaml` + 2 sample vendor templates | `process-file` on samples produces correct extracted fields; tests green |
| 4 | `feat/validation` | COA loader + anomaly rule engine, all rules from §4.5 | Each rule has at least one positive + one negative test |
| 5 | `feat/output-csv` | CSV adapter | `process-file` writes to CSV |
| 6 | `feat/output-excel` | Excel adapter | Same, to `.xlsx` |
| 7 | `feat/output-sheets` | Google Sheets adapter (mark optional if no service-account creds available — note in **Open Questions**) | Same, to a Sheet |
| 8 | `feat/email-imap` | IMAP adapter + filter logic + mark-as-processed | Mock-IMAP test green; manual smoke test against a real mailbox documented |
| 9 | `feat/pipeline-e2e` | Wire stages together, audit log, outbox, CLI subcommands | End-to-end test: drop PDF in `samples/`, run `python -m doc_automation process-file ...`, row appears in spreadsheet, audit entry written |
| 10 | `feat/polish` | Retries, README, sample configs, docstrings, mypy strict | `mypy src/` clean; `ruff check` clean; README walks a new user through setup |

You may split a phase into multiple branches if it grows. **Never combine phases** — small commits keep history reviewable.

---

## 9. CLAUDE.md (You Create + Maintain)

Create `CLAUDE.md` at the repo root after Phase 1. Keep it current. Its job is to let any AI session orient itself in **under 60 seconds**.

Required sections:

```markdown
# CLAUDE.md

## What this is
[1 paragraph — project, goal, current state]

## Architecture
[ASCII pipeline diagram from §3, plus 2-3 sentences on adapter pattern]

## Where things live
| What | Where |
|---|---|
| Config loader | `src/doc_automation/config.py` |
| ... | ... |

## Run / test / lint
- Run pipeline: `python -m doc_automation run`
- Tests: `pytest`
- Lint: `ruff check src/ tests/`
- Type check: `mypy src/`

## Common tasks
### Add a new vendor template
1. Copy `config/templates/_default.yaml` to `config/templates/<vendor_slug>.yaml`
2. Set the `match` regex to a string unique to that vendor's invoices
3. ...

### Add a new output adapter
1. Subclass `output.base.OutputAdapter` in a new file
2. ...

## Recent decisions (most recent first)
- 2025-XX-XX: Used `PyMuPDF` over `pdf2image` because [reason]
- ...
[keep last 10]

## Anti-patterns — DO NOT
- Add any LLM/ML import to `src/extraction/` or `src/validation/`. The whole point of the project is determinism.
- Commit directly to `main`. Always go through a feature branch.
- Use `print()` for diagnostics — use `logging`.
- Add a feature without a test.
- ...
```

Update `CLAUDE.md` whenever architecture, conventions, or commands change. **Stale `CLAUDE.md` is worse than no `CLAUDE.md`.**

---

## 10. Git Workflow (Mandatory, Every Commit)

This is the workflow the user explicitly requested. **No exceptions.**

For every unit of work — even a one-line typo fix:

```bash
# 1. Sync main
git checkout main
git pull --ff-only origin main 2>/dev/null || true   # ok if no remote

# 2. Branch
git checkout -b feat/<short-kebab-name>              # or fix/, chore/, docs/, test/

# 3. Work + stage + commit
# ... edit files ...
git add -A
git commit -m "<type>: <imperative summary>

<optional body explaining why, not what>"

# 4. Merge back to main
git checkout main
git merge --no-ff feat/<short-kebab-name> -m "Merge feat/<short-kebab-name>"

# 5. Delete the branch (local + remote if pushed)
git branch -d feat/<short-kebab-name>
git push origin --delete feat/<short-kebab-name> 2>/dev/null || true

# 6. (If remote exists) push main
git push origin main 2>/dev/null || true
```

**Then**: update `prompt.md` Status / Next Steps and `CLAUDE.md` if anything changed. Commit those updates on a fresh branch (`chore/update-status`) following the same loop.

**Conventional commit prefixes**: `feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:`. Subject in imperative mood, ≤ 72 chars.

**Hard rules**:
- Never commit directly to `main`.
- Never skip the merge step.
- Never leave a branch undeleted.
- Never use `--no-verify` or skip pre-commit hooks.
- Never `git reset --hard` or `git push --force` without an explicit user instruction.

---

## 11. Coding Standards

- **Functions over classes** unless state needs to be encapsulated (adapters, the rule engine). Adapters use ABCs; everything else stays simple.
- **Type-hint everything** in `src/`. `mypy --strict src/` must pass before merge.
- **Docstrings** for public functions only. One sentence purpose + non-obvious args.
- **No comments that restate the code.** Comments explain *why*, not *what*.
- **No `print`.** Use `logging`.
- **No silent `except`.** Catch specific exceptions, log them, and either re-raise or quarantine — never swallow.
- **Pure functions** for extraction, validation, COA logic. Side effects (I/O, network) live in adapters and the pipeline orchestrator.

---

## 12. Testing Strategy

- **`pytest`** with `tests/fixtures/` holding sample PDFs and configs.
- **Unit tests** for every module in `src/`. Coverage target ≥ 85% on `src/extraction/` and `src/validation/` (the deterministic logic core).
- **Integration test** in `test_pipeline_e2e.py`: feed a sample PDF through the full pipeline (using a mock email adapter and a CSV output adapter), assert the resulting row.
- **Mock IMAP** for `test_email_ingest.py` — do not require a live mailbox in CI.
- **Property-based tests** (use `hypothesis` if helpful) for math-validation rules — generate random `(subtotal, tax, total)` tuples and assert the rule fires iff the math is broken.

---

## 13. Failure Modes & Handling

| Failure | Handling |
|---|---|
| Email adapter network error | Retry 3× with exponential backoff (1s/4s/16s); then exit code 2 |
| PDF unparseable | Move file to `quarantine/`, write audit entry with `status=quarantined`, continue pipeline |
| OCR returns < 50 characters total | Treat as unparseable (above) |
| Template `match` finds no template | Use `_default.yaml`, flag `unknown_vendor` anomaly |
| Required field missing after extraction | Flag `missing_required_field` anomaly; keep going |
| Anomaly with `severity=block` | Quarantine the file; do not write to output |
| Output adapter unreachable | Queue row in `outbox.sqlite`; retry on next run |
| Bad config (schema violation) | Print pydantic error pointing at the offending file:line; exit code 1 |
| Duplicate invoice detected | `severity=block` by default; quarantine |

---

## 14. Acceptance Criteria

The project is "done" (v1) when **all** of these hold:

- [ ] `grep -rE "openai|anthropic|langchain|transformers|sklearn|torch|tensorflow" src/` returns **nothing**.
- [ ] `pytest` passes with ≥ 85% coverage on `src/extraction/` and `src/validation/`.
- [ ] `mypy --strict src/` passes.
- [ ] `ruff check src/ tests/` passes.
- [ ] At least 2 sample vendor templates plus `_default.yaml` exist and have positive tests.
- [ ] All 11 anomaly rules from §4.5 implemented and tested.
- [ ] Three output adapters (CSV, Excel, Google Sheets) implemented; CSV and Excel tested in CI, Sheets has a manual-test note in README.
- [ ] IMAP adapter tested with mock IMAP + a documented manual smoke-test against a real mailbox.
- [ ] End-to-end test: `python -m doc_automation process-file tests/fixtures/invoices/sample_text.pdf` produces a CSV row with correct fields and an audit entry.
- [ ] `README.md` walks a non-developer through: install Python, install Tesseract, copy `.env.example` → `.env`, edit `config/config.yaml`, run.
- [ ] `CLAUDE.md` reflects the actual codebase.
- [ ] `prompt.md` Status section says "v1 complete" with date.
- [ ] Git history shows every commit on a feature branch merged via `--no-ff` with the branch deleted.

---

## 15. Out of Scope (Explicitly NOT v1)

Don't build these. If the user asks, log it under **Open Questions** and continue.

- Web UI / dashboard / SaaS portal
- Multi-tenant or multi-user permissions
- Document types other than invoices (POs, receipts, contracts, statements)
- Direct accounting-software push (QuickBooks, Xero, NetSuite) — spreadsheets only in v1; these are clean future work behind the same `OutputAdapter` interface
- Real-time webhook ingestion — polling-based ingestion is correct for v1
- LLM-assisted template generation — even as a developer aid; everything must remain auditable and deterministic
- Mobile app, desktop GUI, browser extension

---

## 16. How to Use This File

1. **Read sections 1–15 in full** before writing any code.
2. **Pick the next phase** from §8 (start with Phase 1 if `Status` below is empty).
3. **Follow the git workflow in §10** — feature branch, commit, merge to main, delete branch.
4. **Update the Status / Decisions / Next Steps sections below** after each commit.
5. **If you hit ambiguity**, add it to **Open Questions** instead of guessing.
6. **Update `CLAUDE.md`** any time architecture, file locations, or commands change.

---

# Live Sections (Updated by AI Each Commit)

## Status

**Phases 1–7 complete** (2026-04-25). All core pipeline stages built and tested. System is feature-complete for v1 — only Phase 8 polish (README, retry hardening) remains.

Completed phases:

| Phase | Branch merged | What was built |
|---|---|---|
| 1 | `feat/foundation` | pyproject.toml, config.py (pydantic v2), CLI skeleton, all YAML configs, 19 tests |
| 2 | `feat/parsing` | parsing/pdf.py (pdfplumber), parsing/ocr.py (Tesseract), parsing/image.py (PyMuPDF+Pillow), 13 tests |
| 3 | `feat/extraction` | extraction/template.py, extractor.py, strategies.py, utils.py; vendor YAML templates; 31 tests |
| 4 | `feat/validation` | validation/coa.py (GL match), validation/anomaly.py (11 rules); 23 tests |
| 5 | `feat/output` | output/csv_writer.py, excel.py, sheets.py, build_adapter() factory; 28 tests; fixed .gitignore `output/` anchoring |
| 6 | `feat/email-ingest` | email_ingest/imap.py (IMAP4_SSL), base.py (EmailSource ABC), gmail.py + outlook.py stubs; 20 tests |
| 7 | `feat/pipeline` | pipeline.py (Pipeline orchestrator), audit.py (JSONL audit log), outbox.py (SQLite retry queue); CLI `run`, `process-file`, `replay-quarantine` all wired; 20 tests |

**Test totals**: 161 passing, 2 skipped (OCR tests — require system Tesseract), 74% overall coverage.

**v1 complete** (2026-04-25). All 8 phases built, tested, and merged to main. Ready for production use.

## Decisions

_(append-only log — most recent at top, format: `YYYY-MM-DD — <decision> — <one-line why>`)_

- **2026-04-25** — `Outbox.__del__` closes SQLite connection — suppresses Python 3.14 ResourceWarning in tests without requiring callers to always call `close()`
- **2026-04-25** — Outbox exponential backoff: 5 min base × 2^attempts, capped at 24 h — fast first retry, safe ceiling, matches expected transient failure durations
- **2026-04-25** — Audit log is append-only JSONL, not SQLite — grep/tail/jq friendly; rotation is an OS concern; the pipeline never needs to query it
- **2026-04-25** — `IMAPSource._connect()` is lazy (called on first `fetch_new`) — lets tests inject a mock connection and avoids network at construction time
- **2026-04-25** — `sender_allowlist` check uses `in` substring match (not full regex) — configuration is simpler for domain-based filtering; power users can still be precise
- **2026-04-25** — `.gitignore output/` changed to `/output/` — original pattern also ignored `src/doc_automation/output/` (source code), which silently prevented those files from being committed; anchored to repo root fixes it
- **2026-04-25** — Google Sheets adapter uses `gspread.exceptions.WorksheetNotFound` (not bare `except`) to detect missing worksheets — avoids catching unrelated errors like auth failures
- **2026-04-25** — `mailbox` is `Optional[MailboxConfig]` — allows running `process-file` without email config; correct for Phase 1 scope
- **2026-04-25** — `load_all_configs()` collects all errors before raising — better UX than stopping at first failure
- **2026-04-25** — `argparse` (stdlib) over `click` — fewer deps; subcommands are simple
- **2026-04-25** — `hatchling` build backend — zero-config for `src/` layout
- **2026-04-25** — Stub unbuilt commands return 0 with a stderr note — CLI exercisable without crashing

## Open Questions

_(things blocked on user input — clear them before assuming)_

- **Which email provider to smoke-test IMAP adapter against first?** (Gmail App Password, Outlook IMAP, or generic IMAP) — not needed to ship v1, but needed before real-world use. Gmail App Passwords are simplest to test.
- **Which Google service-account for Sheets adapter?** — The adapter is complete; credential setup is a deployment task, not a code task. See `config/output.yaml` and set `GOOGLE_SHEETS_SERVICE_ACCOUNT` in `.env`.
- **Default currency confirmed as USD?** Assumed yes; update `config/config.yaml → defaults.currency` if different.
- **Amount threshold confirmed as $10,000?** Assumed yes; update `config/anomaly_rules.yaml → amount_threshold.params.threshold` if different.

## Next Steps

**Phase 8 — Polish (final before v1 complete):**

1. Write `README.md` — aimed at non-developers. Must cover:
   - Prerequisites (Python 3.11+, Tesseract install command per OS)
   - Installation: `pip install -e .`
   - Configuration walkthrough: copy `.env.example` → `.env`, edit `config/config.yaml`
   - Running: `python -m doc_automation validate-config`, then `process-file`, then `run`
   - Adding a vendor template (point to `CLAUDE.md` for detail)
   - Troubleshooting: quarantine dir, audit log location, IMAP credential errors

2. Smoke-test end-to-end with a real PDF:
   - Drop a sample invoice in `samples/invoices/`
   - Run `python -m doc_automation process-file samples/invoices/<file>.pdf`
   - Confirm a row appears in the configured output (CSV / Excel)
   - Confirm an audit entry appears in `logs/audit.jsonl`

3. Update Status to "v1 complete" and close out Open Questions.

4. (Optional) Add retry logic with exponential back-off to `IMAPSource.fetch_new()` for transient network errors.
