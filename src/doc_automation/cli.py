"""Command-line interface for doc-automation."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ── Subcommand handlers ───────────────────────────────────────────────────────


def _cmd_validate_config(args: argparse.Namespace) -> int:
    from doc_automation.config import ConfigError, load_all_configs

    config_dir = Path(args.config_dir)
    try:
        config, rules, coa = load_all_configs(config_dir)
        print(
            f"Config OK: {len(rules.rules)} anomaly rules, "
            f"{len(coa)} chart-of-accounts entries"
        )
        return 0
    except ConfigError as exc:
        print(f"Config errors found:\n{exc}", file=sys.stderr)
        return 1


def _cmd_run(args: argparse.Namespace) -> int:
    from doc_automation.audit import AuditLogger
    from doc_automation.config import ConfigError, load_all_configs, load_output_config
    from doc_automation.email_ingest import build_email_source
    from doc_automation.outbox import Outbox
    from doc_automation.output import build_adapter
    from doc_automation.pipeline import Pipeline

    config_dir = Path(args.config_dir)
    try:
        config, rules, coa = load_all_configs(config_dir)
    except ConfigError as exc:
        print(f"Config errors:\n{exc}", file=sys.stderr)
        return 1

    output_cfg_path = config_dir.parent / config.output.config_file
    output_cfg = load_output_config(output_cfg_path)
    columns = [
        "vendor_name", "invoice_number", "invoice_date", "due_date",
        "currency", "subtotal", "tax_amount", "total",
        "gl_code", "anomaly_flags", "template_used",
        "source_file", "source_email_id", "attachment_sha256", "processed_at",
    ]
    output_adapter = build_adapter(output_cfg, columns)

    email_source = None
    if config.mailbox is not None:
        email_source = build_email_source(config.mailbox)

    audit = AuditLogger(config.paths.audit_log)
    outbox = Outbox(config_dir.parent / "outbox.sqlite")
    templates_dir = config_dir / "templates"

    pipeline = Pipeline(
        config=config,
        rules=rules,
        coa=coa,
        output_adapter=output_adapter,
        email_source=email_source,
        audit_logger=audit,
        outbox=outbox,
        templates_dir=templates_dir,
    )

    result = pipeline.run()
    print(f"Run complete: {result}")
    output_adapter.close()
    if email_source:
        email_source.close()
    outbox.close()
    return 0


def _cmd_process_file(args: argparse.Namespace) -> int:
    from doc_automation.audit import AuditLogger
    from doc_automation.config import ConfigError, load_all_configs, load_output_config
    from doc_automation.output import build_adapter
    from doc_automation.pipeline import Pipeline

    config_dir = Path(args.config_dir)
    try:
        config, rules, coa = load_all_configs(config_dir)
    except ConfigError as exc:
        print(f"Config errors:\n{exc}", file=sys.stderr)
        return 1

    output_cfg_path = config_dir.parent / config.output.config_file
    output_cfg = load_output_config(output_cfg_path)
    columns = [
        "vendor_name", "invoice_number", "invoice_date", "due_date",
        "currency", "subtotal", "tax_amount", "total",
        "gl_code", "anomaly_flags", "template_used",
        "source_file", "source_email_id", "attachment_sha256", "processed_at",
    ]
    output_adapter = build_adapter(output_cfg, columns)
    audit = AuditLogger(config.paths.audit_log)
    templates_dir = config_dir / "templates"

    pipeline = Pipeline(
        config=config,
        rules=rules,
        coa=coa,
        output_adapter=output_adapter,
        audit_logger=audit,
        templates_dir=templates_dir,
    )

    file_path = Path(args.path)
    if not file_path.exists():
        print(f"File not found: {file_path}", file=sys.stderr)
        return 1

    invoice = pipeline.process_file(file_path)

    flags_str = "; ".join(invoice.anomaly_flags) if invoice.anomaly_flags else "none"
    print(f"vendor:   {invoice.vendor_name or '(unknown)'}")
    print(f"invoice:  {invoice.invoice_number or '(unknown)'}")
    print(f"date:     {invoice.invoice_date or '(unknown)'}")
    print(f"total:    {invoice.total or '(unknown)'}")
    print(f"gl_code:  {invoice.gl_code or '(unknown)'}")
    print(f"flags:    {flags_str}")
    print(f"template: {invoice.template_used}")

    try:
        output_adapter.write_rows([invoice])
        print(f"Written to output.")
    except Exception as exc:
        print(f"Output write failed: {exc}", file=sys.stderr)
        return 1
    finally:
        output_adapter.close()

    return 0


def _cmd_list_templates(args: argparse.Namespace) -> int:
    from doc_automation.config import find_templates

    templates_dir = Path(args.config_dir) / "templates"
    names = find_templates(templates_dir)
    if not names:
        print(f"No templates found in {templates_dir}")
        return 0
    for name in names:
        marker = " (default fallback)" if name == "_default" else ""
        print(f"  {name}{marker}")
    return 0


def _cmd_replay_quarantine(args: argparse.Namespace) -> int:
    from doc_automation.audit import AuditLogger
    from doc_automation.config import ConfigError, load_all_configs, load_output_config
    from doc_automation.output import build_adapter
    from doc_automation.pipeline import Pipeline

    config_dir = Path(args.config_dir)
    try:
        config, rules, coa = load_all_configs(config_dir)
    except ConfigError as exc:
        print(f"Config errors:\n{exc}", file=sys.stderr)
        return 1

    quarantine_dir = config.paths.quarantine_dir
    pdfs = sorted(quarantine_dir.glob("*.pdf")) + sorted(quarantine_dir.glob("*.png")) + \
        sorted(quarantine_dir.glob("*.jpg")) + sorted(quarantine_dir.glob("*.tiff"))
    if not pdfs:
        print(f"No files in quarantine ({quarantine_dir})")
        return 0

    output_cfg_path = config_dir.parent / config.output.config_file
    output_cfg = load_output_config(output_cfg_path)
    columns = [
        "vendor_name", "invoice_number", "invoice_date", "due_date",
        "currency", "subtotal", "tax_amount", "total",
        "gl_code", "anomaly_flags", "template_used",
        "source_file", "source_email_id", "attachment_sha256", "processed_at",
    ]
    output_adapter = build_adapter(output_cfg, columns)
    audit = AuditLogger(config.paths.audit_log)
    templates_dir = config_dir / "templates"

    pipeline = Pipeline(
        config=config,
        rules=rules,
        coa=coa,
        output_adapter=output_adapter,
        audit_logger=audit,
        templates_dir=templates_dir,
    )

    ok = 0
    for path in pdfs:
        try:
            invoice = pipeline.process_file(path)
            output_adapter.write_rows([invoice])
            print(f"  replayed: {path.name}")
            ok += 1
        except Exception as exc:
            print(f"  failed:   {path.name}: {exc}", file=sys.stderr)

    output_adapter.close()
    print(f"Replayed {ok}/{len(pdfs)} quarantined files.")
    return 0


# ── Parser ────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc-automation",
        description="Deterministic invoice processing automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config-dir",
        default="config",
        metavar="DIR",
        help="Path to the config directory (default: config)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    subs = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    subs.add_parser(
        "run",
        help="Run the full pipeline: ingest → parse → extract → validate → output",
    )

    subs.add_parser(
        "validate-config",
        help="Validate all configuration files without running the pipeline",
    )

    process = subs.add_parser(
        "process-file",
        help="Run the pipeline on a single local PDF or image (skips email ingestion)",
    )
    process.add_argument("path", metavar="FILE", help="Path to the invoice file")

    subs.add_parser(
        "list-templates",
        help="List all known vendor templates",
    )

    subs.add_parser(
        "replay-quarantine",
        help="Re-process documents in the quarantine directory",
    )

    return parser


_HANDLERS: dict[str, object] = {
    "run": _cmd_run,
    "validate-config": _cmd_validate_config,
    "process-file": _cmd_process_file,
    "list-templates": _cmd_list_templates,
    "replay-quarantine": _cmd_replay_quarantine,
}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    _setup_logging(verbose=getattr(args, "verbose", False))
    handler = _HANDLERS[args.command]
    sys.exit(handler(args))  # type: ignore[operator]
