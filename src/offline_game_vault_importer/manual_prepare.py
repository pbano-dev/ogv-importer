from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import uuid
from typing import Any

from .errors import ImporterError
from .planner import validate_plan
from .prepare import (
    _build_deterministic_tar_gz,
    _copy_item,
    _copy_tree_filtered,
    _inventory,
    _privacy_report,
    _relative_set,
)
from .sanitization import apply_sanitization_rules
from .util import sha256_file, write_json


def _regular_directory(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ImporterError(f"{label} no es un directorio regular")
    return candidate.resolve(strict=True)


def _copy_selected_state(
    workspace: Path,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    destination_root = workspace / "private-state"
    result: list[dict[str, Any]] = []
    for item in plan["persistent_state"]["items"]:
        source_raw = item.get("source_path")
        disposition = item.get("disposition")
        entry = {
            "id": item["id"],
            "path": item.get("path"),
            "disposition": disposition,
            "present_in_source": False,
        }
        if not isinstance(source_raw, str) or not source_raw:
            result.append(entry)
            continue
        source = Path(source_raw).expanduser()
        if source.is_symlink() or not source.exists():
            raise ImporterError(
                f"fuente manual de estado ausente o symlink: {item['id']}"
            )
        source = source.resolve(strict=True)
        entry["present_in_source"] = True
        if disposition in {"exclude", "embedded", "unbound"}:
            result.append(entry)
            continue
        group = item.get("save_set_id") if disposition == "save-set" else disposition
        if not isinstance(group, str) or not group:
            raise ImporterError(f"grupo de estado inválido: {item['id']}")
        destination = destination_root / group / item["id"] / source.name
        _copy_item(source, destination)
        relative = destination.relative_to(workspace).as_posix()
        item["workspace_path"] = relative
        entry["workspace_path"] = relative
        if destination.is_file() and not destination.is_symlink():
            entry["sha256"] = sha256_file(destination)
            entry["size"] = destination.stat().st_size
        result.append(entry)
    return result


def _prepare_manual_in_place(
    plan: dict[str, Any],
    *,
    game: Path,
    prefix: Path | None,
    workspace: Path,
) -> dict[str, Any]:
    plan = validate_plan(plan, phase="prepare")
    if workspace.exists() or workspace.is_symlink():
        raise ImporterError("el workspace manual ya existe")
    workspace.mkdir(parents=True, mode=0o700)

    game = _regular_directory(game, "directorio del juego")
    prefix_root = (
        _regular_directory(prefix, "prefix") if prefix is not None else None
    )

    plan["source"] = {
        **plan.get("source", {}),
        "type": "manual-components-v1",
        "game_directory": str(game),
        "prefix_directory": str(prefix_root) if prefix_root is not None else None,
    }
    write_json(workspace / "IMPORT_PLAN.json", plan)

    neutral = workspace / "neutral-object"
    payload = neutral / "payload"
    game_destination = payload / "game"
    prefix_destination = payload / "prefix-template"

    _copy_tree_filtered(game, game_destination, excludes=set())
    if prefix_root is None:
        (prefix_destination / "drive_c").mkdir(parents=True, exist_ok=True)
    else:
        excludes = []
        for item in plan["persistent_state"]["items"]:
            raw = item.get("path")
            disposition = item.get("disposition")
            if (
                isinstance(raw, str)
                and raw
                and disposition not in {"embedded", "unbound"}
            ):
                excludes.append(raw)
        # If the selected game lives inside the selected prefix, exclude its
        # original location. The canonical object stores it once in payload/game.
        try:
            relative_game = game.relative_to(prefix_root).as_posix()
        except ValueError:
            relative_game = None
        if relative_game:
            excludes.append(relative_game)
        _copy_tree_filtered(
            prefix_root,
            prefix_destination,
            excludes=_relative_set(excludes),
        )

    state_receipt = _copy_selected_state(workspace, plan)
    # Seal the plan after workspace payload paths have been assigned.
    write_json(workspace / "IMPORT_PLAN.json", plan)
    sanitization = apply_sanitization_rules(neutral, plan)
    reports = workspace / "reports"
    write_json(reports / "privacy-sanitization.json", sanitization)
    write_json(
        reports / "state-closure.json",
        {
            "schema": 0,
            "status": (
                "partial"
                if plan["persistent_state"].get("unclassified_candidates")
                else "closed"
            ),
            "unresolved": plan["persistent_state"].get(
                "unclassified_candidates", []
            ),
            "manual_selection": True,
        },
    )
    write_json(
        reports / "state-extraction.json",
        {"schema": 0, "items": state_receipt},
    )

    layout_contract = {
        "schema": 0,
        "contract": "ogv-neutral-game-layout-v1",
        "payload": {
            "game": "payload/game",
            "prefix_template": "payload/prefix-template",
        },
        "materialization": {
            "game_destination_in_prefix": plan["layout"][
                "game_destination_in_prefix"
            ],
            "entrypoint_relative_to_game": plan["layout"]["entrypoint"],
            "working_directory_in_prefix": plan["layout"]["working_directory"],
        },
        "source": {
            "format": "manual-components",
            "bottles_metadata_embedded": False,
        },
        "profiles": plan["profiles"],
    }
    write_json(neutral / "NEUTRAL_LAYOUT.json", layout_contract)
    inventory = _inventory(neutral)
    write_json(neutral / "INVENTORY.json", inventory)
    write_json(
        neutral / "INVENTORY_SEAL.json",
        {
            "schema": 0,
            "inventory_scope": "neutral-object before INVENTORY.json",
            "inventory_sha256": sha256_file(neutral / "INVENTORY.json"),
        },
    )

    privacy = _privacy_report(
        neutral,
        source_root=game.parent,
        package_name=game.name,
    )
    write_json(reports / "privacy-report.json", privacy)

    archive = workspace / "objects/neutral-game.tar.gz"
    _build_deterministic_tar_gz(neutral, archive)
    digest = sha256_file(archive)
    receipt = {
        "schema": 0,
        "operation": "prepare-neutral-import-workspace",
        "source_mode": "manual-components-v1",
        "tool_version": "0.2.1",
        "status": (
            "candidate-needs-privacy-review"
            if privacy["blocking_text_hits"]
            else "candidate-prepared"
        ),
        "source_archive": None,
        "neutral_object": {
            "path": "objects/neutral-game.tar.gz",
            "sha256": digest,
            "bytes": archive.stat().st_size,
        },
        "state_items": state_receipt,
        "privacy_sanitization": sanitization,
        "functional_retest_required": bool(
            sanitization.get("functional_retest_required")
        ),
        "vault_modified": False,
    }
    write_json(workspace / "PREPARE_RECEIPT.json", receipt)
    write_json(
        workspace / "draft/CAPSULE_INPUT.json",
        {
            "schema": 0,
            "contract": "ogv-capsule-input-draft-v2",
            "status": "candidate",
            "identity": plan["identity"],
            "neutral_object": receipt["neutral_object"],
            "runner": plan["runner"],
            "persistent_state": plan["persistent_state"]["items"],
            "profiles": plan["profiles"],
            "not_ready_for_vault_commit": False,
        },
    )
    return receipt


def prepare_manual_workspace(
    plan: dict[str, Any],
    *,
    game: Path,
    prefix: Path | None,
    workspace: Path,
) -> dict[str, Any]:
    final = workspace.expanduser()
    if final.exists() or final.is_symlink():
        raise ImporterError(f"el workspace ya existe: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = final.parent / f".{final.name}.tmp-{uuid.uuid4().hex}"
    try:
        result = _prepare_manual_in_place(
            plan,
            game=game,
            prefix=prefix,
            workspace=staging,
        )
        os.replace(staging, final)
        return result
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
