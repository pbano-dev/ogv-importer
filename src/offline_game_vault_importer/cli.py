from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .errors import ImporterError
from .manual_prepare import prepare_prepared_workspace
from .planner import (
    new_prepared_plan,
    require_prepared_plan_contract,
    validate_plan,
)
from .prepared import inspect_prepared_game
from .util import read_json, write_json
from .vault_commit import commit_workspace
from .verify import verify_workspace
from .workspace_sanitize import sanitize_workspace


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogv-import",
        description=(
            "Importa al Vault un directorio de juego ya desacoplado de Steam. "
            "No aplica Steamless, no sustituye DLLs y no descarga componentes."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser(
        "inspect-game",
        help="Inspecciona un directorio jugable ya desacoplado.",
    )
    inspect.add_argument("--game", required=True, type=Path)
    inspect.add_argument("--title")
    inspect.add_argument("--no-dll-hash", action="store_true")
    inspect.add_argument("--output", type=Path)

    plan = sub.add_parser(
        "new-plan",
        help="Genera un IMPORT_PLAN v3 con nomenclatura sugerida.",
    )
    plan.add_argument("--game", required=True, type=Path)
    plan.add_argument("--title")
    plan.add_argument("--output", required=True, type=Path)

    prepare = sub.add_parser(
        "prepare",
        help="Construye el workspace neutral; nunca escribe en el Vault.",
    )
    prepare.add_argument("--plan", required=True, type=Path)
    prepare.add_argument("--game", required=True, type=Path)
    prepare.add_argument("--prefix", type=Path)
    prepare.add_argument("--workspace", required=True, type=Path)

    verify = sub.add_parser(
        "verify-workspace",
        help="Verifica hashes, inventario, estado y privacidad del workspace.",
    )
    verify.add_argument("--workspace", required=True, type=Path)

    sanitize = sub.add_parser(
        "sanitize-workspace",
        help="Aplica reglas declarativas de privacidad y vuelve a sellar el workspace.",
    )
    sanitize.add_argument("--workspace", required=True, type=Path)
    sanitize.add_argument("--plan", required=True, type=Path)

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

    sub.add_parser("gui", help="Abre el asistente gráfico.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect-game":
            result = inspect_prepared_game(
                args.game,
                title=args.title,
                hash_steam_api=not args.no_dll_hash,
            )
            if args.output:
                write_json(args.output, result)
            _print(result)
        elif args.command == "new-plan":
            inspection = inspect_prepared_game(args.game, title=args.title)
            result = new_prepared_plan(
                title=args.title or inspection["directory_name"],
                game_directory=str(args.game.expanduser().resolve(strict=True)),
            )
            result["layout"]["entrypoint_candidates_relative_to_game"] = [
                item["path"] for item in inspection["executables"]
            ]
            if inspection.get("entrypoint_suggestion"):
                result["layout"]["entrypoint"] = inspection["entrypoint_suggestion"]
            result["inspection"] = {
                "contract": inspection["contract"],
                "file_count": inspection["file_count"],
                "directory_count": inspection["directory_count"],
                "total_bytes": inspection["total_bytes"],
                "steam_api_files": inspection["steam_api_files"],
            }
            write_json(args.output, result)
            _print(result)
        elif args.command == "prepare":
            _print(
                prepare_prepared_workspace(
                    read_json(args.plan, "IMPORT_PLAN.json"),
                    game=args.game,
                    prefix=args.prefix,
                    workspace=args.workspace,
                )
            )
        elif args.command == "verify-workspace":
            _print(verify_workspace(args.workspace))
        elif args.command == "sanitize-workspace":
            _print(
                sanitize_workspace(
                    args.workspace,
                    read_json(args.plan, "IMPORT_PLAN.json"),
                )
            )
        elif args.command == "validate-import":
            plan_value = read_json(args.plan, "IMPORT_PLAN.json")
            require_prepared_plan_contract(plan_value)
            _print(
                {
                    "schema": 0,
                    "status": "valid",
                    "phase": args.phase,
                    "plan": validate_plan(
                        plan_value,
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
