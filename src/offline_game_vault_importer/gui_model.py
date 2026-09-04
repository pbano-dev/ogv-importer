from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .core_bridge import probe_core
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
    ) -> dict[str, Any]:
        """Run the complete safe import pipeline selected in the GUI."""

        if self.vault is None:
            raise ImporterError("falta el Vault de destino")
        prepared = self.prepare(
            game=game,
            prefix=prefix,
            workspace=workspace,
        )
        verified = self.verify()
        dry_run = self.commit(dry_run=True)
        committed = self.commit(dry_run=False)
        return {
            "schema": 0,
            "status": committed.get("status", "candidate-imported"),
            "capsule_id": committed.get("capsule_id"),
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
