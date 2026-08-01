from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from .archive import inspect_tar
from .errors import ImporterError
from .prepare import _inventory
from .util import canonical_json_bytes, read_json, sha256_file


def _component_fingerprint(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.exists():
        raise ImporterError("componente seleccionado ausente o symlink")
    if path.is_file():
        return {
            "kind": "file",
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    inventory = _inventory(path)
    return {
        "kind": "directory",
        "entry_count": len(inventory.get("entries", [])),
        "inventory_sha256": hashlib.sha256(
            canonical_json_bytes(inventory)
        ).hexdigest(),
    }


def _verify_component_receipts(
    root: Path,
    items: list[dict[str, Any]],
    label: str,
) -> int:
    verified = 0
    for item in items:
        raw = item.get("workspace_path")
        expected = item.get("fingerprint")
        if not isinstance(raw, str) or not raw:
            continue
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise ImporterError(f"{label}.{item.get('id')}.workspace_path inseguro")
        candidate = root.joinpath(*pure.parts)
        actual = _component_fingerprint(candidate)
        if isinstance(expected, dict) and actual != expected:
            raise ImporterError(
                f"fingerprint incorrecto: {label}/{item.get('id')}"
            )
        verified += 1
    return verified


def verify_workspace(workspace: Path) -> dict[str, Any]:
    root = workspace.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ImporterError("workspace no es un directorio regular")
    root = root.resolve(strict=True)

    receipt = read_json(root / "PREPARE_RECEIPT.json", "PREPARE_RECEIPT.json")
    plan = read_json(root / "IMPORT_PLAN.json", "IMPORT_PLAN.json")
    neutral = root / "neutral-object"
    archive = root / receipt["neutral_object"].get(
        "path", "objects/neutral-game.tar.gz"
    )
    if not archive.exists():
        archive = root / "objects/neutral-game.tar.gz"
    if archive.is_symlink() or not archive.is_file():
        raise ImporterError("falta objects/neutral-game.tar.gz")
    actual_archive_digest = sha256_file(archive)
    expected_archive_digest = receipt["neutral_object"]["sha256"]
    if actual_archive_digest != expected_archive_digest:
        raise ImporterError(
            "hash del objeto neutral incorrecto: "
            f"{actual_archive_digest} != {expected_archive_digest}"
        )

    inventory = read_json(neutral / "INVENTORY.json", "INVENTORY.json")
    seal = read_json(neutral / "INVENTORY_SEAL.json", "INVENTORY_SEAL.json")
    inventory_digest = sha256_file(neutral / "INVENTORY.json")
    if inventory_digest != seal.get("inventory_sha256"):
        raise ImporterError("sello de INVENTORY.json incorrecto")

    verified_files = 0
    verified_symlinks = 0
    for item in inventory.get("entries", []):
        path = neutral / PurePosixPath(item["path"])
        kind = item["type"]
        if kind == "file":
            if path.is_symlink() or not path.is_file():
                raise ImporterError(f"fichero de inventario ausente: {item['path']}")
            if path.stat().st_size != item["size"]:
                raise ImporterError(f"tamaño incorrecto: {item['path']}")
            digest = sha256_file(path)
            if digest != item["sha256"]:
                raise ImporterError(f"hash incorrecto: {item['path']}")
            verified_files += 1
        elif kind == "directory":
            if path.is_symlink() or not path.is_dir():
                raise ImporterError(f"directorio ausente: {item['path']}")
        elif kind == "symlink":
            if not path.is_symlink():
                raise ImporterError(f"symlink ausente: {item['path']}")
            if os.readlink(path) != item["target"]:
                raise ImporterError(f"destino de symlink incorrecto: {item['path']}")
            verified_symlinks += 1
        else:
            raise ImporterError(f"tipo de inventario desconocido: {kind}")

    forbidden = neutral / "payload/prefix-template/bottle.yml"
    if forbidden.exists() or forbidden.is_symlink():
        raise ImporterError("bottle.yml contaminó el objeto neutral")

    # Confirm declared state is absent from the neutral prefix/game.
    game_destination = PurePosixPath(
        plan["layout"]["game_destination_in_prefix"]
    )
    absent_state: list[str] = []
    for item in plan["persistent_state"]["items"]:
        raw_state_path = item.get("path")
        if (
            not isinstance(raw_state_path, str)
            or not raw_state_path
            or item.get("disposition") in {"unbound", "embedded", "exclude"}
        ):
            continue
        state_path = PurePosixPath(raw_state_path)
        prefix_candidate = neutral / "payload/prefix-template" / state_path
        if prefix_candidate.exists() or prefix_candidate.is_symlink():
            raise ImporterError(
                f"estado aún presente en prefix-template: {item['path']}"
            )
        try:
            game_relative = state_path.relative_to(game_destination)
        except ValueError:
            pass
        else:
            game_candidate = neutral / "payload/game" / game_relative
            if game_candidate.exists() or game_candidate.is_symlink():
                raise ImporterError(
                    f"estado aún presente en payload/game: {item['path']}"
                )
        absent_state.append(item["id"])

    selected_components_verified = 0
    component_report_path = root / "reports/selected-components.json"
    if component_report_path.is_file() and not component_report_path.is_symlink():
        component_report = read_json(
            component_report_path, "selected-components.json"
        )
        for section in ("supplemental_content", "documentation"):
            selected_components_verified += _verify_component_receipts(
                root,
                component_report.get(section, []),
                section,
            )
    selected_components_verified += _verify_component_receipts(
        root,
        receipt.get("state_items", []),
        "persistent_state",
    )
    runner_receipt = receipt.get("runner")
    if isinstance(runner_receipt, dict):
        selected_components_verified += _verify_component_receipts(
            root,
            [runner_receipt],
            "runner",
        )

    public_plan = root / "PUBLIC_IMPORT_PLAN.json"
    if public_plan.is_file() and not public_plan.is_symlink():
        public = read_json(public_plan, "PUBLIC_IMPORT_PLAN.json")
        if public.get("source", {}).get("game_directory") is not None:
            raise ImporterError("PUBLIC_IMPORT_PLAN conserva game_directory")
        for section in ("supplemental_content", "documentation"):
            for item in public.get(section, []):
                if item.get("source_path") is not None:
                    raise ImporterError(
                        f"PUBLIC_IMPORT_PLAN conserva source_path en {section}"
                    )

    archive_report = inspect_tar(archive, full_hash=False)
    return {
        "schema": 0,
        "status": "verified",
        "neutral_object_sha256": actual_archive_digest,
        "neutral_object_bytes": archive.stat().st_size,
        "archive_member_count": archive_report["member_count"],
        "inventory_files_verified": verified_files,
        "inventory_symlinks_verified": verified_symlinks,
        "state_items_absent_from_neutral_object": absent_state,
        "selected_components_verified": selected_components_verified,
        "public_plan_verified": public_plan.is_file(),
        "vault_modified": False,
    }
