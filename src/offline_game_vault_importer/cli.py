from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .archive import inspect_tar
from .errors import ImporterError
from .legacy import scan_package
from .planner import build_plan, new_manual_plan, validate_plan
from .prepare import prepare_workspace
from .manual_prepare import prepare_manual_workspace
from .vault_commit import commit_workspace
from .tree_report import parse_tree_hash_report
from .util import read_json, write_json
from .verify import verify_workspace
from .workspace_sanitize import sanitize_workspace


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogv-import",
        description=(
            "Asistente gráfico y CLI para preparar e importar juegos "
            "como candidatos de OfflineGameVault."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    tree = sub.add_parser(
        "scan-tree-report",
        help="Reconoce un tree-hashes sin tocar el paquete original.",
    )
    tree.add_argument("--report", required=True, type=Path)
    tree.add_argument("--output", type=Path)

    archive = sub.add_parser(
        "inspect-archive",
        help="Inspecciona de forma segura un tar de Bottles.",
    )
    archive.add_argument("--archive", required=True, type=Path)
    archive.add_argument("--full-hash", action="store_true")
    archive.add_argument("--output", type=Path)

    scan = sub.add_parser(
        "scan-package",
        help="Escanea un paquete legacy-preservation-package-v1.",
    )
    scan.add_argument("--source", required=True, type=Path)
    scan.add_argument("--vault", type=Path)
    scan.add_argument("--full-hash", action="store_true")
    scan.add_argument("--skip-archive-inspection", action="store_true")
    scan.add_argument("--output", required=True, type=Path)

    plan = sub.add_parser(
        "init-plan",
        help="Genera IMPORT_PLAN.json editable a partir de un escaneo.",
    )
    plan.add_argument("--scan", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)

    prepare = sub.add_parser(
        "prepare",
        help="Construye un workspace neutral; nunca escribe en el Vault.",
    )
    prepare.add_argument("--plan", required=True, type=Path)
    prepare.add_argument("--source", required=True, type=Path)
    prepare.add_argument("--workspace", required=True, type=Path)

    verify = sub.add_parser(
        "verify-workspace",
        help="Verifica hashes, inventario, estado y contaminación Bottles.",
    )
    verify.add_argument("--workspace", required=True, type=Path)

    sanitize = sub.add_parser(
        "sanitize-workspace",
        help="Aplica reglas declarativas de privacidad y vuelve a sellar el workspace.",
    )
    sanitize.add_argument("--workspace", required=True, type=Path)
    sanitize.add_argument("--plan", required=True, type=Path)

    manual_plan = sub.add_parser(
        "new-manual-plan",
        help="Genera un plan vacío para selección manual de componentes.",
    )
    manual_plan.add_argument("--output", required=True, type=Path)

    manual_prepare = sub.add_parser(
        "prepare-manual",
        help="Construye un workspace desde directorios seleccionados manualmente.",
    )
    manual_prepare.add_argument("--plan", required=True, type=Path)
    manual_prepare.add_argument("--game", required=True, type=Path)
    manual_prepare.add_argument("--prefix", type=Path)
    manual_prepare.add_argument("--workspace", required=True, type=Path)

    validate = sub.add_parser(
        "validate-import",
        help="Valida un plan para GUI, prepare o commit.",
    )
    validate.add_argument("--plan", required=True, type=Path)
    validate.add_argument(
        "--phase", choices=["gui", "prepare", "commit"], default="commit"
    )

    commit = sub.add_parser(
        "commit-vault",
        help="Publica transaccionalmente un workspace como candidato.",
    )
    commit.add_argument("--workspace", required=True, type=Path)
    commit.add_argument("--vault", required=True, type=Path)
    commit.add_argument("--dry-run", action="store_true")

    sub.add_parser(
        "gui",
        help="Abre el asistente gráfico del importador.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "scan-tree-report":
            result = parse_tree_hash_report(args.report)
            if args.output:
                write_json(args.output, result)
            _print(result)
        elif args.command == "inspect-archive":
            result = inspect_tar(args.archive, full_hash=args.full_hash)
            if args.output:
                write_json(args.output, result)
            _print(result)
        elif args.command == "scan-package":
            result = scan_package(
                args.source,
                vault=args.vault,
                inspect_game_archive=not args.skip_archive_inspection,
                full_hash=args.full_hash,
            )
            write_json(args.output, result)
            _print(result)
        elif args.command == "init-plan":
            result = build_plan(read_json(args.scan, "scan"))
            write_json(args.output, result)
            _print(result)
        elif args.command == "prepare":
            result = prepare_workspace(
                read_json(args.plan, "IMPORT_PLAN.json"),
                source_package=args.source,
                workspace=args.workspace,
            )
            _print(result)
        elif args.command == "verify-workspace":
            _print(verify_workspace(args.workspace))
        elif args.command == "sanitize-workspace":
            _print(
                sanitize_workspace(
                    args.workspace,
                    read_json(args.plan, "IMPORT_PLAN.json"),
                )
            )
        elif args.command == "new-manual-plan":
            result = new_manual_plan()
            write_json(args.output, result)
            _print(result)
        elif args.command == "prepare-manual":
            result = prepare_manual_workspace(
                read_json(args.plan, "IMPORT_PLAN.json"),
                game=args.game,
                prefix=args.prefix,
                workspace=args.workspace,
            )
            _print(result)
        elif args.command == "validate-import":
            _print(
                {
                    "schema": 0,
                    "status": "valid",
                    "phase": args.phase,
                    "plan": validate_plan(
                        read_json(args.plan, "IMPORT_PLAN.json"),
                        phase=args.phase,
                    ),
                }
            )
        elif args.command == "commit-vault":
            _print(
                commit_workspace(
                    args.workspace,
                    vault=args.vault,
                    dry_run=args.dry_run,
                )
            )
        elif args.command == "gui":
            from .gui import main as gui_main
            return gui_main([])
        else:
            raise ImporterError(f"comando no implementado: {args.command}")
        return 0
    except (ImporterError, OSError, ValueError, KeyError) as exc:
        print(f"ogv-import: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
