from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Callable

from .core_bridge import probe_core
from .errors import ImporterError
from .manual_prepare import prepare_prepared_workspace
from .naming import is_portable_id
from .planner import (
    new_prepared_plan,
    require_prepared_plan_contract,
    validate_plan,
)
from .prepared import inspect_prepared_game
from .util import read_json, write_json
from .vault_commit import commit_workspace
from .verify import verify_workspace


def _format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _existing_ancestor(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.exists():
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _relative_path_problem(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
    ):
        return "debe contener una ruta relativa portable"
    path = PurePosixPath(value)
    if path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        return "debe ser relativa y no contener . ni .."
    if value in {"[RELLENAR]", "[VERIFICAR]", "[NO PROBADO]", "[NO APLICA]"}:
        return "sigue pendiente"
    return None


@dataclass(slots=True)
class ImportSession:
    plan: dict[str, Any] | None = None
    plan_path: Path | None = None
    workspace: Path | None = None
    vault: Path | None = None
    game_directory: Path | None = None
    inspection: dict[str, Any] | None = None
    messages: list[str] = field(default_factory=list)

    def load_plan(self, path: Path) -> dict[str, Any]:
        value = read_json(path, "IMPORT_PLAN.json")
        require_prepared_plan_contract(value)
        self.plan = validate_plan(value, phase="gui")
        self.plan_path = path.expanduser().resolve(strict=True)
        self.messages.append(f"Plan cargado: {self.plan_path}")
        return self.plan

    def save_plan(self, path: Path | None = None) -> Path:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        target = path or self.plan_path
        if target is None:
            raise ImporterError("indique una ruta para guardar el plan")
        target = target.expanduser()
        require_prepared_plan_contract(self.plan)
        write_json(target, validate_plan(self.plan, phase="gui"))
        self.plan_path = target.resolve(strict=True)
        self.messages.append(f"Plan guardado: {self.plan_path}")
        return self.plan_path

    def new_prepared(
        self,
        *,
        game: Path | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        game_text = str(game) if game is not None else None
        self.plan = new_prepared_plan(
            title=title,
            game_directory=game_text,
        )
        self.plan_path = None
        if game is not None:
            self.game_directory = game.expanduser().resolve(strict=True)
            self.inspection = inspect_prepared_game(
                self.game_directory,
                title=title,
            )
            self._apply_inspection(self.inspection)
        self.messages.append("Plan de juego desacoplado creado")
        return self.plan

    def inspect_game(
        self,
        game: Path,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        root = game.expanduser().resolve(strict=True)
        inspection = inspect_prepared_game(root, title=title)
        self.game_directory = root
        self.inspection = inspection
        if self.plan is None:
            self.plan = new_prepared_plan(
                title=title,
                game_directory=str(root),
            )
        self._apply_inspection(inspection)
        self.messages.append(f"Directorio inspeccionado: {root}")
        return inspection

    def _apply_inspection(self, inspection: dict[str, Any]) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        source = self.plan.setdefault("source", {})
        source["game_directory"] = str(self.game_directory) if self.game_directory else None
        suggestions = inspection["naming_suggestions"]
        self.plan["naming_examples"] = suggestions
        identity = self.plan["identity"]
        if identity.get("title") in {"", "[RELLENAR]", "game"}:
            identity["title"] = inspection["directory_name"]
        if identity.get("capsule_id") in {"", "[RELLENAR]", "game"}:
            identity["capsule_id"] = suggestions["capsule_id"]
        layout = self.plan["layout"]
        layout["entrypoint_candidates_relative_to_game"] = [
            item["path"] for item in inspection["executables"]
        ]
        if inspection.get("entrypoint_suggestion") and layout.get("entrypoint") in {
            "",
            "[RELLENAR]",
        }:
            layout["entrypoint"] = inspection["entrypoint_suggestion"]
        if layout.get("game_destination_in_prefix") in {
            "",
            "[RELLENAR]",
            "drive_c/Games/game",
        }:
            layout["game_destination_in_prefix"] = suggestions[
                "game_destination_in_prefix"
            ]
            layout["working_directory"] = suggestions[
                "game_destination_in_prefix"
            ]

    def set_identity(
        self,
        *,
        title: str,
        edition: str,
        store: str,
        appid: str,
        version: str,
        capsule_id: str,
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        self.plan["identity"].update(
            {
                "title": title.strip(),
                "edition": edition.strip(),
                "source_store": store.strip(),
                "appid": appid.strip(),
                "preserved_version": version.strip(),
                "capsule_id": capsule_id.strip(),
            }
        )

    def set_layout(
        self,
        *,
        entrypoint: str,
        game_destination: str,
        working_directory: str,
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        self.plan["layout"].update(
            {
                "entrypoint": entrypoint.strip(),
                "game_destination_in_prefix": game_destination.strip(),
                "working_directory": working_directory.strip(),
            }
        )

    def set_runner(
        self,
        *,
        binding: str,
        source_path: str | None,
        preferred_id: str | None,
        digest: str | None,
        source_root: str | None = None,
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        self.plan["runner"].update(
            {
                "binding": binding,
                "source_path": source_path or None,
                "preferred_id": preferred_id or None,
                "sha256": digest or None,
                "source_root": source_root or None,
            }
        )

    def add_state(
        self,
        *,
        state_id: str,
        source_path: str | None,
        destination_path: str | None,
        disposition: str,
        kind: str,
        save_set_id: str | None = None,
        display_name: str | None = None,
        required: bool = False,
        sensitive: bool = True,
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        item: dict[str, Any] = {
            "id": state_id.strip(),
            "path": destination_path.strip() if destination_path else None,
            "kind": kind,
            "disposition": disposition,
            "source_path": source_path or None,
            "required": bool(required),
            "sensitive": bool(sensitive),
        }
        if disposition == "save-set":
            item["save_set_id"] = (save_set_id or state_id).strip()
            item["display_name"] = (display_name or save_set_id or state_id).strip()
        self.plan["persistent_state"]["items"].append(item)

        # A detected candidate becomes classified when the user explicitly
        # assigns the same destination. Detection is advisory; the right-hand
        # selection is authoritative.
        matched_detected_candidate = False
        if destination_path:
            normalized = destination_path.strip()
            pending = self.plan["persistent_state"].get(
                "unclassified_candidates", []
            )
            matched_detected_candidate = normalized in pending
            self.plan["persistent_state"]["unclassified_candidates"] = [
                candidate for candidate in pending
                if candidate != normalized
            ]
        unresolved = self.plan["persistent_state"].get(
            "unclassified_candidates", []
        )
        embedded = any(
            value.get("disposition") in {"embedded", "unbound"}
            for value in self.plan["persistent_state"]["items"]
            if isinstance(value, dict)
        )
        if matched_detected_candidate and not unresolved and not embedded:
            self.plan["persistent_state"]["baseline_state"] = "clean"

    def remove_state(self, state_id: str) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        items = self.plan["persistent_state"]["items"]
        self.plan["persistent_state"]["items"] = [
            item for item in items if item.get("id") != state_id
        ]

    def add_supplemental(
        self,
        *,
        item_id: str,
        source_path: str | None,
        classification: str = "supplemental-content",
        description: str = "",
        placement_mode: str = "sidecar",
        destination: str | None = None,
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        normalized_id = item_id.strip()
        self.plan.setdefault("supplemental_content", []).append(
            {
                "id": normalized_id,
                "source_path": source_path or None,
                "classification": classification,
                "description": description,
                "placement": {
                    "mode": placement_mode,
                    "destination": (
                        destination
                        if destination is not None
                        else normalized_id
                    ),
                },
            }
        )

    def add_documentation(
        self,
        *,
        item_id: str,
        source_path: str | None,
        role: str,
        canonical_name: str,
        description: str = "",
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        self.plan.setdefault("documentation", []).append(
            {
                "id": item_id.strip(),
                "source_path": source_path or None,
                "role": role,
                "canonical_name": canonical_name.strip(),
                "description": description,
            }
        )

    def set_profile(
        self,
        *,
        adapter: str,
        profile_id: str,
        enabled: bool,
    ) -> None:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        for profile in self.plan["profiles"]:
            if profile.get("adapter") == adapter:
                profile["id"] = profile_id.strip()
                profile["enabled"] = bool(enabled)
                return
        raise ImporterError(f"perfil no encontrado para adapter={adapter}")

    def validate(self, phase: str = "gui") -> dict[str, Any]:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        self.plan = validate_plan(self.plan, phase=phase)
        return {
            "schema": 0,
            "status": "valid",
            "phase": phase,
            "runner_binding": self.plan["runner"]["binding"],
            "state_item_count": len(
                self.plan["persistent_state"]["items"]
            ),
            "unclassified_state_count": len(
                self.plan["persistent_state"].get(
                    "unclassified_candidates", []
                )
            ),
            "profile_count": sum(
                1 for item in self.plan["profiles"] if item.get("enabled")
            ),
        }

    def validate_configuration(self, *, check_core: bool = True) -> dict[str, Any]:
        """Validate the complete GUI selection without mutating the Vault."""

        checks: list[dict[str, Any]] = []

        def record(
            check_id: str,
            label: str,
            status: str,
            detail: str,
            action: str | None = None,
        ) -> None:
            item: dict[str, Any] = {
                "id": check_id,
                "label": label,
                "status": status,
                "detail": detail,
            }
            if action:
                item["action"] = action
            checks.append(item)

        if self.plan is None:
            record(
                "plan",
                "Plan de importación",
                "error",
                "No hay ningún plan cargado.",
                "Seleccione el juego y pulse Inspeccionar y proponer.",
            )
            return {
                "schema": 0,
                "status": "invalid",
                "errors": 1,
                "warnings": 0,
                "checks": checks,
                "ready_for_prepare": False,
                "ready_for_commit": False,
            }

        identity = self.plan.get("identity", {})
        identity_issues: list[str] = []
        required_identity = (
            ("title", "título"),
            ("source_store", "tienda"),
            ("preserved_version", "versión"),
            ("capsule_id", "Capsule ID"),
        )
        pending_markers = {
            "[RELLENAR]", "[VERIFICAR]", "[NO PROBADO]", "[NO APLICA]",
        }
        for key, label in required_identity:
            value = identity.get(key) if isinstance(identity, dict) else None
            if not isinstance(value, str) or not value.strip():
                identity_issues.append(f"{label}: vacío")
            elif value in pending_markers:
                identity_issues.append(f"{label}: pendiente")
        capsule_id = (
            identity.get("capsule_id")
            if isinstance(identity, dict) else None
        )
        if (
            isinstance(capsule_id, str)
            and capsule_id not in pending_markers
            and not is_portable_id(capsule_id)
        ):
            identity_issues.append(
                "Capsule ID: use minúsculas, números, puntos, guiones o _"
            )
        record(
            "identity-fields",
            "Campos de identidad",
            "error" if identity_issues else "ok",
            (
                "; ".join(identity_issues)
                if identity_issues
                else "Título, tienda, versión y Capsule ID completos."
            ),
            "Corrija todos los campos enumerados."
            if identity_issues else None,
        )

        layout = self.plan.get("layout", {})
        layout_issues: list[str] = []
        layout_fields = (
            ("entrypoint", "ejecutable"),
            ("game_destination_in_prefix", "destino del juego"),
            ("working_directory", "directorio de trabajo"),
        )
        for key, label in layout_fields:
            raw = layout.get(key) if isinstance(layout, dict) else None
            problem = _relative_path_problem(raw)
            if problem:
                layout_issues.append(f"{label}: {problem}")
        record(
            "layout-fields",
            "Rutas de materialización",
            "error" if layout_issues else "ok",
            (
                "; ".join(layout_issues)
                if layout_issues
                else "Las tres rutas son relativas y portables."
            ),
            "Use rutas como drive_c/Games/elden-ring."
            if layout_issues else None,
        )

        runner = self.plan.get("runner", {})
        runner_issues: list[str] = []
        preferred_id = (
            runner.get("preferred_id")
            if isinstance(runner, dict) else None
        )
        if preferred_id and (
            not isinstance(preferred_id, str) or not is_portable_id(preferred_id)
        ):
            runner_issues.append("el ID del runner no es portable")
        runner_digest = (
            runner.get("sha256")
            if isinstance(runner, dict) else None
        )
        if isinstance(runner_digest, str) and runner_digest.startswith("sha256:"):
            runner_digest = runner_digest.removeprefix("sha256:")
        if runner_digest is not None and runner_digest != "" and (
            not isinstance(runner_digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", runner_digest) is None
        ):
            runner_issues.append("el SHA-256 debe tener 64 dígitos hexadecimales")
        record(
            "runner-fields",
            "Runner opcional",
            "error" if runner_issues else "ok",
            "; ".join(runner_issues) if runner_issues else "Configuración válida.",
            "Corrija el ID o deje el SHA-256 vacío para calcularlo."
            if runner_issues else None,
        )

        try:
            normalized = validate_plan(self.plan, phase="commit")
        except Exception as exc:
            record(
                "plan",
                "Contrato del plan",
                "error",
                str(exc),
                "Revise las comprobaciones de campos y componentes.",
            )
        else:
            self.plan = normalized
            record(
                "plan",
                "Contrato del plan",
                "ok",
                "El contrato, los identificadores y las rutas relativas son válidos.",
            )

        raw_game = self.plan.get("source", {}).get("game_directory")
        game_root: Path | None = self.game_directory
        if game_root is None and isinstance(raw_game, str) and raw_game:
            game_root = Path(raw_game)
        resolved_game: Path | None = None
        if game_root is None:
            record(
                "game",
                "Directorio del juego",
                "error",
                "No se ha seleccionado el directorio del juego.",
            )
        else:
            candidate = game_root.expanduser()
            if candidate.is_symlink() or not candidate.is_dir():
                record(
                    "game",
                    "Directorio del juego",
                    "error",
                    f"No es un directorio regular: {candidate}",
                )
            else:
                resolved_game = candidate.resolve(strict=True)
                record(
                    "game",
                    "Directorio del juego",
                    "ok",
                    str(resolved_game),
                )

        if resolved_game is not None:
            entrypoint = self.plan.get("layout", {}).get("entrypoint")
            if (
                isinstance(entrypoint, str)
                and _relative_path_problem(entrypoint) is None
            ):
                executable = resolved_game.joinpath(
                    *PurePosixPath(entrypoint).parts
                )
                if executable.is_symlink() or not executable.is_file():
                    record(
                        "entrypoint",
                        "Ejecutable",
                        "error",
                        f"No existe dentro del juego: {entrypoint}",
                        "La ruta debe ser relativa a la carpeta de juego seleccionada.",
                    )
                else:
                    record(
                        "entrypoint",
                        "Ejecutable",
                        "ok",
                        entrypoint,
                    )

        appid = self.plan.get("identity", {}).get("appid")
        if appid is not None and appid != "" and not str(appid).isdigit():
            record(
                "appid",
                "AppID",
                "warning",
                f"El AppID no es numérico: {appid}",
                "Déjelo vacío si la tienda no utiliza un identificador numérico.",
            )
        else:
            record(
                "appid",
                "AppID",
                "ok",
                "Vacío" if appid is None or appid == "" else str(appid),
            )

        raw_prefix = self.plan.get("source", {}).get("prefix_directory")
        if isinstance(raw_prefix, str) and raw_prefix:
            prefix = Path(raw_prefix).expanduser()
            if prefix.is_symlink() or not prefix.is_dir():
                record(
                    "prefix",
                    "Prefix inicial",
                    "error",
                    f"No es un directorio regular: {prefix}",
                )
            elif not (prefix / "drive_c").is_dir():
                record(
                    "prefix",
                    "Prefix inicial",
                    "error",
                    "El directorio no contiene drive_c.",
                    "Déjelo vacío si el juego es autocontenido.",
                )
            else:
                record("prefix", "Prefix inicial", "ok", str(prefix))
        else:
            record(
                "prefix",
                "Prefix inicial",
                "ok",
                "No seleccionado; el Core creará uno al materializar.",
            )

        prepared_workspace = False
        workspace_plan_matches = True
        workspace = self.workspace
        if workspace is None:
            record(
                "workspace",
                "Workspace",
                "error",
                "No se ha indicado un workspace nuevo.",
            )
        else:
            workspace = workspace.expanduser()
            if workspace.is_symlink():
                record(
                    "workspace",
                    "Workspace",
                    "error",
                    "El workspace no puede ser un symlink.",
                )
            elif workspace.exists():
                markers = (
                    workspace / "PREPARE_RECEIPT.json",
                    workspace / "IMPORT_PLAN.json",
                    workspace / "objects/neutral-game.tar.gz",
                )
                if not workspace.is_dir() or not all(path.is_file() for path in markers):
                    record(
                        "workspace",
                        "Workspace",
                        "error",
                        "La ruta ya existe pero no es un workspace preparado.",
                        "Para una importación nueva, indique una ruta que todavía no exista.",
                    )
                else:
                    try:
                        verification = verify_workspace(workspace)
                    except Exception as exc:
                        record(
                            "workspace",
                            "Workspace preparado",
                            "error",
                            str(exc),
                        )
                    else:
                        prepared_workspace = True
                        record(
                            "workspace",
                            "Workspace preparado",
                            "ok",
                            (
                                f"{verification['inventory_files_verified']} archivos "
                                "verificados; el objeto inmutable coincide."
                            ),
                        )
                        privacy_path = workspace / "reports/privacy-report.json"
                        try:
                            privacy = read_json(
                                privacy_path, "privacy-report.json"
                            )
                        except Exception as exc:
                            record(
                                "privacy",
                                "Privacidad",
                                "error",
                                str(exc),
                            )
                        else:
                            hits = int(privacy.get("blocking_text_hits", 0))
                            record(
                                "privacy",
                                "Privacidad",
                                "error" if hits else "ok",
                                (
                                    f"{hits} hallazgos bloqueantes en "
                                    f"{privacy.get('blocking_files', 0)} archivos."
                                    if hits
                                    else "No hay rutas privadas bloqueantes."
                                ),
                                (
                                    "Revise reports/privacy-report.json."
                                    if hits else None
                                ),
                            )
                        try:
                            persisted_plan = validate_plan(
                                read_json(
                                    workspace / "IMPORT_PLAN.json",
                                    "IMPORT_PLAN.json",
                                ),
                                phase="commit",
                            )
                        except Exception as exc:
                            workspace_plan_matches = False
                            record(
                                "workspace-plan",
                                "Plan preparado",
                                "error",
                                str(exc),
                            )
                        else:
                            workspace_plan_matches = persisted_plan == self.plan
                            record(
                                "workspace-plan",
                                "Plan preparado",
                                "ok" if workspace_plan_matches else "error",
                                (
                                    "La configuración coincide con el workspace."
                                    if workspace_plan_matches
                                    else (
                                        "La configuración se modificó después de "
                                        "preparar el workspace."
                                    )
                                ),
                                (
                                    None if workspace_plan_matches else
                                    "Prepare un workspace nuevo con estos cambios."
                                ),
                            )
            else:
                ancestor = _existing_ancestor(workspace)
                if not ancestor.is_dir() or not os.access(ancestor, os.W_OK):
                    record(
                        "workspace",
                        "Workspace nuevo",
                        "error",
                        f"No se puede escribir bajo {ancestor}.",
                    )
                else:
                    record(
                        "workspace",
                        "Workspace nuevo",
                        "ok",
                        f"Se creará: {workspace}",
                    )

        vault_root = self.vault.expanduser() if self.vault is not None else None
        if vault_root is None:
            record("vault", "Vault de destino", "error", "No se ha seleccionado.")
        elif vault_root.is_symlink() or not vault_root.is_dir():
            record(
                "vault",
                "Vault de destino",
                "error",
                f"No es un directorio regular: {vault_root}",
            )
        else:
            required = (
                vault_root / "INDEX.json",
                vault_root / "COLLECTION_LAYOUT.json",
                vault_root / "01_IMMUTABLE_VAULT/VAULT_INVENTORY.json",
            )
            missing = [str(path.relative_to(vault_root)) for path in required if not path.is_file()]
            if missing:
                record(
                    "vault",
                    "Vault de destino",
                    "error",
                    "Faltan: " + ", ".join(missing),
                )
            else:
                record("vault", "Vault de destino", "ok", str(vault_root))

        component_failures: list[str] = []
        component_count = 0
        selections: list[tuple[str, Any]] = []
        selections.extend(
            (
                f"estado {item.get('id')}",
                (
                    item.get("workspace_path")
                    if prepared_workspace and item.get("workspace_path")
                    else item.get("source_path")
                ),
            )
            for item in self.plan.get("persistent_state", {}).get("items", [])
        )
        selections.extend(
            (
                f"contenido {item.get('id')}",
                (
                    item.get("workspace_path")
                    if prepared_workspace and item.get("workspace_path")
                    else item.get("source_path")
                ),
            )
            for item in self.plan.get("supplemental_content", [])
        )
        selections.extend(
            (
                f"documento {item.get('id')}",
                (
                    item.get("workspace_path")
                    if prepared_workspace and item.get("workspace_path")
                    else item.get("source_path")
                ),
            )
            for item in self.plan.get("documentation", [])
        )
        selections.append(("runner", self.plan.get("runner", {}).get("source_path")))
        for label, raw_source in selections:
            if not isinstance(raw_source, str) or not raw_source:
                continue
            component_count += 1
            source = Path(raw_source).expanduser()
            if not source.is_absolute() and workspace is not None:
                source = workspace / source
            if source.is_symlink() or not source.exists():
                component_failures.append(f"{label}: {source}")
        record(
            "components",
            "Partidas y componentes",
            "error" if component_failures else "ok",
            (
                "No se encuentran: " + "; ".join(component_failures)
                if component_failures
                else f"{component_count} fuentes seleccionadas disponibles."
            ),
        )

        if check_core:
            try:
                core = probe_core(self.plan)
            except Exception as exc:
                record("core", "OfflineGameVault Core", "error", str(exc))
            else:
                record(
                    "core",
                    "OfflineGameVault Core",
                    "ok",
                    f"{core.version} mediante {core.source}",
                )

        game_bytes: int | None = None
        if self.inspection is not None:
            value = self.inspection.get("total_bytes")
            if isinstance(value, int) and value >= 0:
                game_bytes = value
        if (
            game_bytes is not None
            and workspace is not None
            and not prepared_workspace
            and vault_root is not None
            and vault_root.is_dir()
        ):
            workspace_volume = _existing_ancestor(workspace)
            try:
                same_volume = (
                    workspace_volume.stat().st_dev == vault_root.stat().st_dev
                )
                workspace_free = shutil.disk_usage(workspace_volume).free
                vault_free = shutil.disk_usage(vault_root).free
            except OSError as exc:
                record("storage", "Espacio disponible", "warning", str(exc))
            else:
                if same_volume:
                    needed = int(game_bytes * 3.1)
                    enough = workspace_free >= needed
                    detail = (
                        f"Juego: {_format_bytes(game_bytes)}; libre: "
                        f"{_format_bytes(workspace_free)}; pico adicional "
                        f"estimado: {_format_bytes(needed)}."
                    )
                else:
                    workspace_needed = int(game_bytes * 2.1)
                    vault_needed = int(game_bytes * 1.1)
                    enough = (
                        workspace_free >= workspace_needed
                        and vault_free >= vault_needed
                    )
                    detail = (
                        f"Workspace: {_format_bytes(workspace_free)} libres / "
                        f"{_format_bytes(workspace_needed)} estimados; Vault: "
                        f"{_format_bytes(vault_free)} libres / "
                        f"{_format_bytes(vault_needed)} estimados."
                    )
                record(
                    "storage",
                    "Espacio disponible",
                    "ok" if enough else "error",
                    detail,
                    None if enough else "Libere espacio o elija otro volumen.",
                )
        else:
            record(
                "storage",
                "Espacio disponible",
                "warning",
                (
                    "No se calculó una estimación nueva."
                    if not prepared_workspace
                    else "El workspace ya está preparado."
                ),
            )

        errors = sum(item["status"] == "error" for item in checks)
        warnings = sum(item["status"] == "warning" for item in checks)
        return {
            "schema": 0,
            "status": "valid" if errors == 0 else "invalid",
            "errors": errors,
            "warnings": warnings,
            "checks": checks,
            "ready_for_prepare": errors == 0 and not prepared_workspace,
            "ready_for_commit": (
                errors == 0 and prepared_workspace and workspace_plan_matches
            ),
        }

    def prepare(
        self,
        *,
        game: Path,
        prefix: Path | None,
        workspace: Path,
    ) -> dict[str, Any]:
        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        result = prepare_prepared_workspace(
            self.plan,
            game=game,
            prefix=prefix,
            workspace=workspace,
        )
        self.game_directory = game.expanduser().resolve(strict=True)
        self.workspace = workspace.expanduser().resolve(strict=True)
        self.plan_path = self.workspace / "IMPORT_PLAN.json"
        self.plan = read_json(self.plan_path, "IMPORT_PLAN.json")
        return result

    def prepare_manual(
        self,
        *,
        game: Path,
        prefix: Path | None,
        workspace: Path,
    ) -> dict[str, Any]:
        """Compatibility alias for callers of 0.2.x."""
        return self.prepare(game=game, prefix=prefix, workspace=workspace)

    def verify(self) -> dict[str, Any]:
        if self.workspace is None:
            raise ImporterError("no hay workspace seleccionado")
        return verify_workspace(self.workspace)

    def check_core(self) -> dict[str, object]:
        return probe_core(self.plan).to_dict()

    def import_prepared_game(
        self,
        *,
        game: Path,
        prefix: Path | None,
        workspace: Path,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        """Run the complete safe import pipeline selected in the GUI."""

        if self.plan is None:
            raise ImporterError("no hay un plan cargado")
        if self.vault is None:
            raise ImporterError("falta el Vault de destino")
        self.game_directory = game
        self.workspace = workspace
        self.plan.setdefault("source", {})["game_directory"] = str(game)
        self.plan["source"]["prefix_directory"] = (
            str(prefix) if prefix is not None else None
        )
        preflight = self.validate_configuration()
        if preflight["status"] != "valid":
            details = "; ".join(
                item["detail"]
                for item in preflight["checks"]
                if item["status"] == "error"
            )
            raise ImporterError("configuración general inválida: " + details)
        if progress is not None:
            progress("Preparando y empaquetando el workspace", 1, 4)
        prepared = self.prepare(
            game=game,
            prefix=prefix,
            workspace=workspace,
        )
        if progress is not None:
            progress("Verificando integridad y privacidad", 2, 4)
        verified = self.verify()
        if progress is not None:
            progress("Ensayando la importación", 3, 4)
        dry_run = self.commit(dry_run=True)
        if progress is not None:
            progress("Publicando en el Vault", 4, 4)
        committed = self.commit(dry_run=False)
        return {
            "schema": 0,
            "status": committed.get("status", "candidate-imported"),
            "capsule_id": committed.get("capsule_id"),
            "preflight": preflight,
            "prepare": prepared,
            "verify": verified,
            "dry_run": dry_run,
            "commit": committed,
        }

    def commit(self, *, dry_run: bool = False) -> dict[str, Any]:
        if self.workspace is None or self.vault is None:
            raise ImporterError("faltan workspace o vault")
        # The workspace copy is authoritative at commit time.
        if self.plan is not None:
            write_json(
                self.workspace / "IMPORT_PLAN.json",
                validate_plan(self.plan, phase="commit"),
            )
        return commit_workspace(
            self.workspace,
            vault=self.vault,
            dry_run=dry_run,
        )
