from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

from .errors import ImporterError


MINIMUM_CORE = (0, 19, 7)
REQUIRED_COMMANDS = (
    "audit-capsule",
    "preserve-state",
    "verify-state-backup",
    "ingest-object",
    "inventory",
    "list-optional-content",
    "compose",
)
_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")


@dataclass(frozen=True, slots=True)
class CoreInvocation:
    command: tuple[str, ...]
    environment: dict[str, str]
    source: str


@dataclass(frozen=True, slots=True)
class CoreProbe:
    version: str
    version_tuple: tuple[int, int, int]
    source: str
    commands: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "version_tuple": list(self.version_tuple),
            "source": self.source,
            "commands": list(self.commands),
            "minimum_version": ".".join(str(item) for item in MINIMUM_CORE),
        }


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


def _run_probe_command(
    invocation: CoreInvocation,
    arguments: list[str],
    *,
    label: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            [*invocation.command, *arguments],
            env=invocation.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ImporterError(
            "no se localiza el núcleo oficial OfflineGameVault; "
            "configure core.source_root, OGV_SOURCE_ROOT o core.command"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ImporterError(f"el núcleo agotó el tiempo al comprobar {label}") from exc
    except OSError as exc:
        raise ImporterError(f"no se pudo consultar el núcleo: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ImporterError(
            f"el núcleo no admite {label}: {detail or 'sin diagnóstico'}"
        )
    return completed


def probe_core(plan: dict[str, Any] | None = None) -> CoreProbe:
    """Verify the exact public Core contract required by an import.

    The importer is deliberately a client of the official CLI.  Refusing an
    old or incomplete checkout before staging any Vault mutation keeps that
    boundary explicit and prevents a partially compatible command set from
    producing a capsule that the current GUI cannot consume.
    """

    invocation = resolve_core(plan)
    version_result = _run_probe_command(
        invocation,
        ["--version"],
        label="--version",
    )
    version = version_result.stdout.strip()
    match = _VERSION_RE.search(version)
    if match is None:
        raise ImporterError(
            "el núcleo devolvió una versión no reconocible: " + version[:200]
        )
    version_tuple = tuple(int(item) for item in match.groups())
    if version_tuple < MINIMUM_CORE:
        minimum = ".".join(str(item) for item in MINIMUM_CORE)
        raise ImporterError(
            f"OfflineGameVault Core {version} es demasiado antiguo; "
            f"se requiere {minimum} o posterior"
        )

    for command in REQUIRED_COMMANDS:
        _run_probe_command(
            invocation,
            [command, "--help"],
            label=command,
        )
    return CoreProbe(
        version=version,
        version_tuple=version_tuple,
        source=invocation.source,
        commands=REQUIRED_COMMANDS,
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
    return probe_core(plan).version
