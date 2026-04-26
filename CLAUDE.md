# CLAUDE.md — AI Codebase Map

> Read this before writing any code. Update it whenever architecture, file locations, or commands change. Stale CLAUDE.md is worse than none.

## What this is

A deterministic, AI-free invoice processing pipeline. Invoices arrive by email → the system extracts key fields (vendor, invoice #, date, totals, line items, tax) → validates against a chart of accounts → applies rule-based anomaly detection → writes clean rows to spreadsheets (CSV, Excel, or Google Sheets). No LLMs, no ML models, no probabilistic logic anywhere in extraction or validation. Tesseract OCR is the one permitted "AI-adjacent" tool (it's deterministic and local). See `prompt.md` for the full mission, constraints, and implementation phases.

## Architecture

```
email_ingest  -->  parsing  -->  extraction  -->  coa_validation  -->  anomaly_detection  -->  output
(IMAP/Gmail/      (PDF/OCR)     (template        (GL code            (11 named rules)         (CSV/Excel/
 Outlook)                        per vendor)       mapping)                                      Sheets)
                                                                           |                        |
                                                                           v                        v
                                                                       audit log (JSONL)       outbox (SQLite)
```

Adapter pattern at the edges: every email source and every output destination implements a base interface. Core logic (extraction, validation, anomaly detection) is pure functions with no I/O.

## Where things live

| What | Where |
|---|---|
| Config pydantic models + loaders | `src/doc_automation/config.py` |
| CLI entry point | `src/doc_automation/cli.py` (+ `__main__.py`) |
| Pipeline orchestrator | `src/doc_automation/pipeline.py` |
| Audit log writer | `src/doc_automation/audit.py` |
| SQLite outbox (retry queue) | `src/doc_automation/outbox.py` |
| Email adapters | `src/doc_automation/email_ingest/` |
| PDF/OCR parsing | `src/doc_automation/parsing/` |
| Template-based extraction | `src/doc_automation/extraction/` |
| COA + anomaly validation | `src/doc_automation/validation/` |
| Output adapters | `src/doc_automation/output/` |
| Main config (edit to configure) | `config/config.yaml` |
| 11 anomaly rules | `config/anomaly_rules.yaml` |
| Chart of accounts | `config/chart_of_accounts.csv` |
| Output adapter config | `config/output.yaml` |
| Default vendor template | `config/templates/_default.yaml` |
| Test fixtures (invoices, configs) | `tests/fixtures/` |
| Sample invoices for manual testing | `samples/invoices/` |

## Run / test / lint

```bash
# Activate venv first:
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

# Install (first time):
pip install -e ".[dev]"

# Validate all config files:
python -m doc_automation validate-config

# List known vendor templates:
python -m doc_automation list-templates

# Process a single local invoice PDF:
python -m doc_automation process-file samples/invoices/sample.pdf

# Run full email-to-spreadsheet pipeline:
python -m doc_automation run

# Tests:
pytest

# Lint:
ruff check src/ tests/

# Type-check:
mypy src/
```

## Common tasks

### Add a new vendor template
1. Copy `config/templates/_default.yaml` to `config/templates/<vendor_slug>.yaml`
   - `vendor_slug` = lowercase, hyphens, no spaces (e.g., `acme-corp`)
2. Set `match` to a regex that uniquely identifies that vendor in the raw extracted text
   (matched case-insensitively against the full document text)
3. Set `priority` > 0 (higher = tried first; default template is priority 0)
4. Override any field patterns that differ from the default
5. Test: `python -m doc_automation process-file samples/invoices/<vendor_invoice>.pdf`
6. Add at least one positive and one negative test in `tests/test_extraction.py`

### Add a GL code to the chart of accounts
1. Edit `config/chart_of_accounts.csv`
2. Add a row: `gl_code,name,vendor_match_regex,keyword_match_regex,false`
   - Exactly one row must have `default_for_unmatched=true` — don't change that
3. Verify: `python -m doc_automation validate-config`

### Add a new output adapter
1. Create `src/doc_automation/output/<name>.py`
2. Subclass `output.base.OutputAdapter` and implement `write_rows()`
3. Register it in `output/__init__.py`
4. Add `<name>` to the `valid_adapters` set in `config.py:load_output_config()`
5. Add tests in `tests/test_output.py`

### Add a new anomaly rule
1. Add an entry to `config/anomaly_rules.yaml` (name, severity, description, params)
2. Implement the check in `src/doc_automation/validation/anomaly.py` keyed on `rule.name`
3. Add positive + negative tests in `tests/test_anomaly.py`

## v1 status (2026-04-26)

- **257 tests passing**, 2 skipped (OCR — require system Tesseract), 94% total coverage
- `pipeline.py`, `anomaly.py`, `coa.py` 100%; `cli.py` 98%
- **`mypy --strict`** clean across all 32 source files
- **`ruff check`** clean (61 issues fixed in Phase 8)
- All 8 phases complete + post-Phase-7 hardening (dedup, IMAP retry, line item extraction)
- Type stubs installed: `types-PyYAML`, `types-openpyxl`, `types-python-dateutil`

## Recent decisions (most recent first)

- **2026-04-26** — `Pipeline._process_attachment` outbox guard changed from `if self._outbox:` to `if self._outbox is not None:` — `Outbox.__len__` returns 0 when empty, so the old guard silently dropped invoices from retry queue when the queue was empty at the time of the write failure
- **2026-04-25** — `_COL_SYNONYMS` checks `unit_price` before `quantity` — "Unit Price" contains "unit" matching `units?`; more-specific pattern must come first
- **2026-04-25** — `extract_line_items` dispatched separately from `extract_field` — returns `list[LineItem]` not `str | None`; handled as special case in `apply_template()`
- **2026-04-25** — `LineItem` imported via `TYPE_CHECKING` in `strategies.py` and locally at runtime inside `extract_line_items` — avoids `strategies → invoice` circular import
- **2026-04-25** — `raw_tables: list[list[list[list[str|None]]]]` added to `ParsedDocument` — pdfplumber table data for line-item extraction; populated only by `extract_text_pdf`, not OCR path
- **2026-04-25** — `Outbox.__del__` closes SQLite to suppress Python 3.14 ResourceWarning without requiring callers to always call `close()`
- **2026-04-25** — Audit log is append-only JSONL (not SQLite) — grep/tail/jq friendly; pipeline never needs to query it
- **2026-04-25** — `.gitignore output/` changed to `/output/` — original pattern matched `src/doc_automation/output/` silently preventing source files from being committed
- **2026-04-25** — `IMAPSource._connect()` is lazy — lets tests inject a mock connection; avoids network at construction time
- **2026-04-25** — Google Sheets adapter catches `gspread.exceptions.WorksheetNotFound` specifically — not a bare `except`, so auth failures aren't swallowed
- **2026-04-25** — Used `hatchling` as build backend (over setuptools) — modern, zero-config for src layout
- **2026-04-25** — `mailbox` is `Optional[MailboxConfig]` in `Config` — allows running `process-file` without configuring email
- **2026-04-25** — `load_all_configs()` collects all validation errors before raising — user sees all issues at once
- **2026-04-25** — Used `argparse` (stdlib) for CLI over `click` — avoids an extra dep; subcommands are simple enough

## Anti-patterns — DO NOT

- **Add any LLM/ML import to `src/extraction/` or `src/validation/`** — the entire point of the project is determinism. If you think you need one, add it to Open Questions in `prompt.md` first.
- **Commit directly to `main`** — always use a feature branch → merge --no-ff → delete.
- **Use `print()` for diagnostics** — use `logging`. The CLI uses `print()` only for its final user-facing output line.
- **Swallow exceptions silently** — catch specific exceptions, log them, and either re-raise or quarantine. Never bare `except: pass`.
- **Add a feature without a test** — especially in `config.py`, `validation/`, and `extraction/`.
- **Hard-code paths** — all paths come from the `PathsConfig` pydantic model loaded from `config.yaml`.
- **Use `--no-verify`** on commits or `git push --force` to main.
