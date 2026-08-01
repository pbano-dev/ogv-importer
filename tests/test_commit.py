from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from offline_game_vault_importer.errors import ImporterError
from offline_game_vault_importer.gui_model import ImportSession
from offline_game_vault_importer.legacy import scan_package
from offline_game_vault_importer.planner import build_plan, new_manual_plan, validate_plan
from offline_game_vault_importer.prepare import prepare_workspace
from offline_game_vault_importer.util import sha256_file, write_json
from offline_game_vault_importer.vault_commit import _capsule_document, commit_workspace

from test_importer import make_legacy_package


FAKE_CORE = r"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

def arg(name):
    return Path(sys.argv[sys.argv.index(name)+1])

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

if "--version" in sys.argv:
    print("offline-game-vault 0.9.0-fake")
    raise SystemExit(0)

command = sys.argv[1]
if command == "audit-capsule":
    print(json.dumps({"valid": True}))
elif command == "preserve-state":
    capsule = json.loads(arg("--capsule").read_text())
    state_root = arg("--state-root")
    backup = arg("--backup")
    if backup.exists():
        print("backup exists", file=sys.stderr)
        raise SystemExit(2)
    backup.mkdir(parents=True)
    (backup / "payload").mkdir()
    items = []
    declarations = sorted(
        [x for x in capsule.get("persistent_state", []) if x.get("backup", True)],
        key=lambda x: x["id"],
    )
    if not declarations:
        print(
            "Capsule declares no persistent state with backup enabled",
            file=sys.stderr,
        )
        raise SystemExit(2)
    for index, declaration in enumerate(declarations):
        source = state_root / declaration["path"]
        if source.is_file():
            container = backup / "payload" / f"{index:04d}-{declaration['id']}"
            container.mkdir()
            target = container / "data"
            shutil.copy2(source, target)
            digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
            items.append({
                "id": declaration["id"],
                "declared_path": declaration["path"],
                "kind": declaration["kind"],
                "sensitive": declaration.get("sensitive", False),
                "required": declaration.get("required", False),
                "present": True,
                "entry_type": "file",
                "payload_path": f"payload/{index:04d}-{declaration['id']}/data",
                "file_count": 1,
                "directory_count": 0,
                "bytes": target.stat().st_size,
                "tree_digest": digest,
                "entries": [{
                    "path": ".",
                    "type": "file",
                    "mode": 0o600,
                    "bytes": target.stat().st_size,
                    "digest": digest,
                }],
            })
        else:
            items.append({
                "id": declaration["id"],
                "declared_path": declaration["path"],
                "kind": declaration["kind"],
                "sensitive": declaration.get("sensitive", False),
                "required": declaration.get("required", False),
                "present": False,
                "entry_type": "missing",
            })
    document = {
        "schema": 0,
        "backup_id": f"state-backup-{uuid.uuid4()}",
        "capsule_id": capsule["capsule_id"],
        "complete": True,
        "items": items,
    }
    write(backup / "state-backup.json", document)
    print(json.dumps({"backup_id": document["backup_id"], "complete": True}))
elif command == "verify-state-backup":
    backup = arg("--backup")
    document = json.loads((backup / "state-backup.json").read_text())
    print(json.dumps({"verified": document.get("complete") is True}))
elif command == "ingest-object":
    source = arg("--source")
    vault = arg("--vault-root")
    digest = sys.argv[sys.argv.index("--digest")+1].removeprefix("sha256:")
    destination = vault / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)
    print(json.dumps({
        "status": "present-and-verified",
        "sha256": digest,
        "path": str(destination),
    }))
elif command == "inventory":
    vault = arg("--vault-root")
    output = arg("--output")
    objects = []
    root = vault / "objects" / "sha256"
    if root.exists():
        for item in sorted(root.rglob("*")):
            if item.is_file():
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
                objects.append({
                    "digest": "sha256:" + digest,
                    "bytes": item.stat().st_size,
                    "path": item.relative_to(vault).as_posix(),
                })
    write(output, {
        "schema": 0,
        "algorithm": "sha256",
        "object_count": len(objects),
        "total_bytes": sum(item["bytes"] for item in objects),
        "objects": objects,
    })
    print(json.dumps({"written": str(output), "object_count": len(objects)}))
else:
    print(f"unsupported {command}", file=sys.stderr)
    raise SystemExit(2)
"""


def make_vault(root: Path) -> Path:
    for directory in (
        "01_IMMUTABLE_VAULT/objects/sha256",
        "02_CAPSULES",
        "03_PERSISTENT_STATE",
        "04_RECEIPTS/_collection",
        "05_PRIVATE_WORKSPACES",
        "06_DERIVED_MATERIALIZATIONS",
        "07_EXPORTS",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    write_json(root / "INDEX.json", {"schema": 0, "objects": [], "capsules": []})
    write_json(
        root / "COLLECTION_LAYOUT.json",
        {"schema": 0, "capsule_ids": [], "directories": [], "registrations": []},
    )
    write_json(
        root / "01_IMMUTABLE_VAULT/VAULT_INVENTORY.json",
        {"schema": 0, "object_count": 0, "objects": []},
    )
    (root / "COLLECTION_SHA256.txt").write_text(
        "# OfflineGameVault collection manifest\n",
        encoding="utf-8",
    )
    write_json(
        root / "03_PERSISTENT_STATE/SAVE_LIBRARY_CONTRACT.json",
        {
            "schema": 0,
            "contract": "ogv-save-library-v2",
            "default_selection": "none",
        },
    )
    return root


def confirmed_plan(scan, fake_core: Path):
    plan = build_plan(scan)
    plan["status"] = "confirmed"
    plan["identity"] = {
        "title": "Test Game",
        "edition": "Standard",
        "source_store": "Steam",
        "appid": "123456",
        "preserved_version": "1.0",
        "capsule_id": "steam-123456-test-game-1.0",
    }
    plan["layout"]["entrypoint"] = "TestGame.exe"
    plan["runner"].update({
        "binding": "select-at-materialization",
        "sha256": None,
        "source_path": None,
        "preferred_id": None,
    })
    save_root = "drive_c/users/steamuser/AppData/Roaming/TestGame"
    plan["persistent_state"].update({
        "baseline_state": "clean",
        "unclassified_candidates": [],
        "related_file_exceptions": [],
        "items": [
            {
                "id": "main-save",
                "path": f"{save_root}/save.dat",
                "kind": "save",
                "disposition": "save-set",
                "save_set_id": "principal",
                "display_name": "Principal",
                "required": False,
                "sensitive": True,
            },
            {
                "id": "main-save-backup",
                "path": f"{save_root}/save.dat.bak",
                "kind": "save",
                "disposition": "save-set",
                "save_set_id": "principal",
                "display_name": "Principal",
                "required": False,
                "sensitive": True,
            },
        ],
    })
    plan["core"]["command"] = f"{sys.executable} -S {fake_core}"
    return plan


class PlanTests(unittest.TestCase):
    def test_manual_plan_allows_unbound_runner(self):
        plan = new_manual_plan()
        plan["identity"].update({
            "title": "Manual Game",
            "edition": "Standard",
            "source_store": "Steam",
            "appid": "999",
            "preserved_version": "1.0",
            "capsule_id": "steam-999-manual-game-1.0",
        })
        plan["layout"].update({
            "game_destination_in_prefix": "drive_c/Games/Manual",
            "working_directory": "drive_c/Games/Manual",
            "entrypoint": "Manual.exe",
        })
        normalized = validate_plan(plan, phase="commit")
        self.assertEqual(
            normalized["runner"]["binding"],
            "select-at-materialization",
        )


    def test_manual_classification_closes_detected_state(self):
        plan = new_manual_plan()
        path = "drive_c/users/steamuser/AppData/Roaming/Game/save.dat"
        plan["persistent_state"]["unclassified_candidates"] = [path]
        plan["persistent_state"]["baseline_state"] = "embedded-or-unknown"
        session = ImportSession(plan=plan)
        session.add_state(
            state_id="main-save",
            source_path=None,
            destination_path=path,
            disposition="save-set",
            kind="save",
            save_set_id="principal",
        )
        self.assertEqual(
            session.plan["persistent_state"]["unclassified_candidates"], []
        )
        self.assertEqual(
            session.plan["persistent_state"]["baseline_state"], "clean"
        )

    def test_manual_state_does_not_claim_clean_without_detection(self):
        session = ImportSession(plan=new_manual_plan())
        session.add_state(
            state_id="main-save",
            source_path=None,
            destination_path=(
                "drive_c/users/steamuser/AppData/Roaming/Game/save.dat"
            ),
            disposition="save-set",
            kind="save",
            save_set_id="principal",
        )
        self.assertEqual(
            session.plan["persistent_state"]["baseline_state"],
            "embedded-or-unknown",
        )

    def test_windows_profile_does_not_depend_on_runner(self):
        plan = new_manual_plan()
        plan["identity"].update({
            "title": "Manual Game",
            "edition": "Standard",
            "source_store": "Steam",
            "appid": "999",
            "preserved_version": "1.0",
            "capsule_id": "steam-999-manual-game-1.0",
        })
        plan["layout"].update({
            "game_destination_in_prefix": "drive_c/Games/Manual",
            "working_directory": "drive_c/Games/Manual",
            "entrypoint": "Manual.exe",
        })
        game_object = {
            "id": "game-baseline",
            "digest": "sha256:" + "b" * 64,
            "roles": ["game_payload", "prefix_baseline"],
            "format": "tar.gz",
            "required": True,
            "archive_path": "objects/sha256/bb/bb/" + "b" * 64,
            "shared": False,
        }
        for profile in plan["profiles"]:
            profile["enabled"] = True
        runner_object = {
            "id": "proton9",
            "digest": "sha256:" + "a" * 64,
            "roles": ["runner"],
            "format": "tar.gz",
            "required": True,
            "archive_path": "objects/sha256/aa/aa/" + "a" * 64,
            "shared": True,
        }
        capsule = _capsule_document(
            plan=validate_plan(plan, phase="commit"),
            game_object=game_object,
            runner_object=runner_object,
        )
        profiles = {item["adapter"]: item for item in capsule["profiles"]}
        self.assertIn("proton9", profiles["wine"]["dependencies"])
        self.assertIn("proton9", profiles["bottles"]["dependencies"])
        self.assertNotIn("proton9", profiles["windows"]["dependencies"])


class CommitTests(unittest.TestCase):
    def _workspace(self, root: Path):
        package = make_legacy_package(root / "package")
        scan = scan_package(package, full_hash=True)
        fake = root / "fake_core.py"
        fake.write_text(FAKE_CORE, encoding="utf-8")
        plan = confirmed_plan(scan, fake)
        workspace = root / "workspace"
        prepare_workspace(plan, source_package=package, workspace=workspace)
        return workspace, plan

    def test_dry_run_supports_multifile_save_and_no_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _ = self._workspace(root)
            vault = make_vault(root / "vault")
            result = commit_workspace(workspace, vault=vault, dry_run=True)
            self.assertEqual(result["status"], "dry-run-valid")
            self.assertEqual(result["state"]["save_set_count"], 1)
            self.assertIsNone(result["objects"]["runner"])
            self.assertFalse((vault / "02_CAPSULES/steam-123456-test-game-1.0").exists())

    def test_dry_run_packages_manual_runner_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _ = self._workspace(root)
            runner = root / "proton9"
            (runner / "files/bin").mkdir(parents=True)
            (runner / "files/bin/wine").write_text("#!/bin/sh\n", encoding="utf-8")
            (runner / "files/bin/wineserver").write_text("#!/bin/sh\n", encoding="utf-8")
            (runner / "files/bin/wine").chmod(0o755)
            (runner / "files/bin/wineserver").chmod(0o755)
            plan_path = workspace / "IMPORT_PLAN.json"
            plan = json.loads(plan_path.read_text())
            plan["runner"].update({
                "binding": "preferred",
                "source_path": str(runner),
                "preferred_id": "proton9",
                "sha256": None,
            })
            write_json(plan_path, plan)
            vault = make_vault(root / "vault")
            result = commit_workspace(workspace, vault=vault, dry_run=True)
            declaration = result["objects"]["runner"]
            self.assertIsNotNone(declaration)
            self.assertEqual(declaration["id"], "proton9")
            self.assertEqual(declaration["format"], "tar.gz")
            self.assertTrue(result["objects"]["runner_new"])
            self.assertFalse((vault / "02_CAPSULES").joinpath(result["capsule_id"]).exists())

    def test_dry_run_rejects_nested_symlink_in_supplemental_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _ = self._workspace(root)
            external = root / "outside.txt"
            external.write_text("private", encoding="utf-8")
            supplemental = root / "supplemental"
            supplemental.mkdir()
            (supplemental / "escape").symlink_to(external)

            plan_path = workspace / "IMPORT_PLAN.json"
            plan = json.loads(plan_path.read_text())
            plan["supplemental_content"] = [{
                "id": "manual-bonus",
                "classification": "supplemental-content",
                "source_path": str(supplemental),
            }]
            write_json(plan_path, plan)

            vault = make_vault(root / "vault")
            with self.assertRaises(ImporterError):
                commit_workspace(workspace, vault=vault, dry_run=True)

    def test_commit_publishes_candidate_and_multifile_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, _ = self._workspace(root)
            vault = make_vault(root / "vault")
            result = commit_workspace(workspace, vault=vault)
            self.assertEqual(result["status"], "candidate-imported")
            capsule_id = result["capsule_id"]
            capsule = json.loads(
                (vault / f"02_CAPSULES/{capsule_id}/capsule.json").read_text()
            )
            self.assertEqual(
                {profile["status"] for profile in capsule["profiles"]},
                {"candidate", "not_tested"},
            )
            manifest = json.loads(
                (
                    vault
                    / f"03_PERSISTENT_STATE/{capsule_id}/save-sets/principal/save-set.json"
                ).read_text()
            )
            self.assertEqual(len(manifest["items"]), 2)
            self.assertEqual(manifest["status"], "candidate")
            index = json.loads((vault / "INDEX.json").read_text())
            self.assertTrue(
                any(item.get("capsule_id") == capsule_id for item in index["capsules"])
            )
            object_path = (
                vault / "01_IMMUTABLE_VAULT"
                / next(
                    item["archive_path"]
                    for item in capsule["objects"]
                    if "game_payload" in item["roles"]
                )
            )
            self.assertTrue(object_path.is_file())
            manifest_text = (vault / "COLLECTION_SHA256.txt").read_text()
            receipt_rel = result["receipt"]
            self.assertIn(receipt_rel, manifest_text)
            receipt_digest = sha256_file(vault / receipt_rel)
            self.assertIn(receipt_digest, manifest_text)
            public_template = (
                vault / f"02_CAPSULES/{capsule_id}/evidence/"
                "source-bottles/bottle.yml"
            )
            private_template = (
                vault / f"05_PRIVATE_WORKSPACES/{capsule_id}/archived/"
                "source-bottles/bottle.yml"
            )
            self.assertTrue(public_template.is_file())
            self.assertTrue(private_template.is_file())


if __name__ == "__main__":
    unittest.main()
