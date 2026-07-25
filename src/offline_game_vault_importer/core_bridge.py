from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

from .errors import ImporterError


@dataclass(frozen=True, slots=True)
class CoreInvocation:
    command: tuple[str, ...]
    environment: dict[str, str]
    source: str


def resolve_core(plan: dict[str, Any] | None = None) -> CoreInvocation:
    """Resolve the official OfflineGameVault core without downloading anything."""
    core = plan.get("core", {}) if isinstance(plan, dict) else {}
    explicit_command = core.get("command") if isinstance(core, dict) else None
    explicit_root = core.get("source_root") if isinstance(core, dict) else None

    if isinstance(explicit_command, str) and explicit_command.strip():
        command = tuple(shlex.split(explicit_command))
        if not command:
            raise ImporterError("core.command está vacío")
        return CoreInvocation(
            command=command,
            environment=dict(os.environ),
            source="plan.core.command",
        )

    configured_root = explicit_root or os.environ.get("OGV_SOURCE_ROOT")
    if isinstance(configured_root, str) and configured_root.strip():
        root = Path(configured_root).expanduser()
        if root.is_symlink() or not root.is_dir():
            raise ImporterError(
                "core.source_root/OGV_SOURCE_ROOT no es un checkout regular"
            )
        root = root.resolve(strict=True)
        module = root / "src/offline_game_vault/cli.py"
        if not module.is_file() or module.is_symlink():
            raise ImporterError(
                "el checkout del núcleo no contiene src/offline_game_vault/cli.py"
            )
        environment = dict(os.environ)
        source_path = str(root / "src")
        previous = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_path if not previous else source_path + os.pathsep + previous
        )
        return CoreInvocation(
            command=(sys.executable, "-m", "offline_game_vault.cli"),
            environment=environment,
            source=f"source-root:{root}",
        )

    return CoreInvocation(
        command=("ogv",),
        environment=dict(os.environ),
        source="PATH:ogv",
    )


def run_core_json(
    arguments: list[str],
    *,
    plan: dict[str, Any] | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    invocation = resolve_core(plan)
    command = [*invocation.command, *arguments, "--json"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            env=invocation.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImporterError(
            "no se localiza el núcleo oficial OfflineGameVault; "
            "configure core.source_root, OGV_SOURCE_ROOT o core.command"
        ) from exc
    except OSError as exc:
        raise ImporterError(f"no se pudo ejecutar el núcleo: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ImporterError(
            f"el núcleo terminó con código {completed.returncode}: {detail}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ImporterError(
            "el núcleo no devolvió JSON válido: " + completed.stdout[:300]
        ) from exc
    if not isinstance(value, dict):
        raise ImporterError("el núcleo devolvió un JSON no-objeto")
    return value


def core_version(plan: dict[str, Any] | None = None) -> str:
    invocation = resolve_core(plan)
    try:
        completed = subprocess.run(
            [*invocation.command, "--version"],
            env=invocation.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise ImporterError(f"no se pudo consultar el núcleo: {exc}") from exc
    if completed.returncode != 0:
        raise ImporterError(
            "el núcleo no respondió a --version: "
            + (completed.stderr or completed.stdout).strip()
        )
    return completed.stdout.strip()
