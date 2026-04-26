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
    # Implemented in Phase 7 (end-to-end pipeline)
    print("'run' is not yet implemented (Phase 7)", file=sys.stderr)
    return 0


def _cmd_process_file(args: argparse.Namespace) -> int:
    # Implemented in Phase 3+ (extraction) and wired in Phase 7
    print("'process-file' is not yet implemented (Phase 3)", file=sys.stderr)
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
    # Implemented in Phase 7
    print("'replay-quarantine' is not yet implemented (Phase 7)", file=sys.stderr)
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
