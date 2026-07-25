from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from .errors import ImporterError
from .planner import validate_plan
from .prepare import (
    _build_deterministic_tar_gz,
    _inventory,
    _privacy_report,
)
from .sanitization import apply_sanitization_rules
from .util import read_json, sha256_file, write_json
from .verify import verify_workspace


def _same_source_identity(old: dict[str, Any], new: dict[str, Any]) -> None:
    checks = [
        ("source.game_archive_sha256", old["source"]["game_archive_sha256"], new["source"]["game_archive_sha256"]),
        ("source.game_archive", old["source"]["game_archive"], new["source"]["game_archive"]),
        ("identity.capsule_id", old["identity"]["capsule_id"], new["identity"]["capsule_id"]),
        ("layout.game_root_in_archive", old["layout"]["game_root_in_archive"], new["layout"]["game_root_in_archive"]),
    ]
    mismatches = [
        f"{label}: {before!r} != {after!r}"
        for label, before, after in checks
        if before != after
    ]
    if mismatches:
        raise ImporterError(
            "el plan de saneamiento no corresponde al workspace: "
            + "; ".join(mismatches)
        )


def sanitize_workspace(
    workspace: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    root = workspace.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ImporterError("workspace no es un directorio regular")
    root = root.resolve(strict=True)

    validate_plan(plan)
    previous_verification = verify_workspace(root)
    old_plan = read_json(root / "IMPORT_PLAN.json", "IMPORT_PLAN.json")
    _same_source_identity(old_plan, plan)

    neutral = root / "neutral-object"
    if neutral.is_symlink() or not neutral.is_dir():
        raise ImporterError("neutral-object no es un directorio regular")

    sanitization = apply_sanitization_rules(neutral, plan)
    write_json(root / "reports/privacy-sanitization.json", sanitization)

    inventory_path = neutral / "INVENTORY.json"
    seal_path = neutral / "INVENTORY_SEAL.json"
    inventory_path.unlink(missing_ok=True)
    seal_path.unlink(missing_ok=True)

    inventory = _inventory(neutral)
    write_json(inventory_path, inventory)
    write_json(
        seal_path,
        {
            "schema": 0,
            "inventory_scope": "neutral-object before INVENTORY.json",
            "inventory_sha256": sha256_file(inventory_path),
        },
    )

    privacy = _privacy_report(
        neutral,
        source_root=None,
        package_name=plan["source"]["package_name"],
    )
    write_json(root / "reports/privacy-report.json", privacy)

    receipt = read_json(root / "PREPARE_RECEIPT.json", "PREPARE_RECEIPT.json")
    object_path = root / receipt["neutral_object"].get(
        "path", "objects/neutral-game.tar.gz"
    )
    _build_deterministic_tar_gz(neutral, object_path)
    object_digest = sha256_file(object_path)
    object_bytes = object_path.stat().st_size

    write_json(root / "IMPORT_PLAN.json", plan)

    draft_path = root / "draft/CAPSULE_INPUT.json"
    draft = read_json(draft_path, "CAPSULE_INPUT.json")
    draft["neutral_object"]["sha256"] = object_digest
    draft["neutral_object"]["bytes"] = object_bytes
    blockers = [
        item
        for item in draft.get("blockers", [])
        if item not in {
            "Resolver hallazgos de privacidad, si existen.",
            "Revalidar representación de texto: se retiraron referencias a fuentes externas ausentes del prefix.",
        }
    ]
    if privacy["blocking_text_hits"]:
        blockers.append("Resolver hallazgos de privacidad, si existen.")
    if sanitization.get("functional_retest_required"):
        blockers.append(
            "Revalidar representación de texto: se retiraron referencias "
            "a fuentes externas ausentes del prefix."
        )
    draft["blockers"] = blockers
    write_json(draft_path, draft)

    receipt["tool_version"] = "0.1.2"
    receipt["status"] = (
        "candidate-needs-privacy-review"
        if privacy["blocking_text_hits"]
        else "candidate-prepared"
    )
    receipt["neutral_object"]["sha256"] = object_digest
    receipt["neutral_object"]["bytes"] = object_bytes
    receipt["privacy_sanitization"] = sanitization
    receipt["functional_retest_required"] = sanitization.get(
        "functional_retest_required", False
    )
    write_json(root / "PREPARE_RECEIPT.json", receipt)

    final_verification = verify_workspace(root)
    return {
        "schema": 0,
        "operation": "sanitize-neutral-import-workspace",
        "tool_version": "0.1.2",
        "status": (
            "verified-needs-privacy-review"
            if privacy["blocking_text_hits"]
            else "verified-privacy-clean"
        ),
        "previous_neutral_object_sha256": previous_verification[
            "neutral_object_sha256"
        ],
        "neutral_object_sha256": final_verification[
            "neutral_object_sha256"
        ],
        "neutral_object_bytes": final_verification[
            "neutral_object_bytes"
        ],
        "privacy": privacy,
        "sanitization": sanitization,
        "vault_modified": False,
    }
