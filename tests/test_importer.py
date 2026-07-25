from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
import zipfile

from offline_game_vault_importer.archive import inspect_tar, safe_extract_tar
from offline_game_vault_importer.errors import ImporterError
from offline_game_vault_importer.legacy import scan_package
from offline_game_vault_importer.planner import build_plan
from offline_game_vault_importer.prepare import prepare_workspace
from offline_game_vault_importer.tree_report import parse_tree_hash_report
from offline_game_vault_importer.util import sha256_file, write_json
from offline_game_vault_importer.verify import verify_workspace
from offline_game_vault_importer.workspace_sanitize import sanitize_workspace


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes, mode: int = 0o644):
    item = tarfile.TarInfo(name)
    item.size = len(payload)
    item.mode = mode
    archive.addfile(item, io.BytesIO(payload))


def _add_dir(archive: tarfile.TarFile, name: str, mode: int = 0o755):
    item = tarfile.TarInfo(name.rstrip("/") + "/")
    item.type = tarfile.DIRTYPE
    item.mode = mode
    archive.addfile(item)


def _add_symlink(archive: tarfile.TarFile, name: str, target: str):
    item = tarfile.TarInfo(name)
    item.type = tarfile.SYMTYPE
    item.linkname = target
    item.mode = 0o777
    archive.addfile(item)


def make_bottle_archive(path: Path, *, include_backup: bool = True, private_registry: bool = False, unknown_private_registry: bool = False) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for directory in (
            "Bottle",
            "Bottle/drive_c",
            "Bottle/drive_c/Games",
            "Bottle/drive_c/Games/TestGame",
            "Bottle/drive_c/Games/TestGame/steam_settings",
            "Bottle/drive_c/users",
            "Bottle/drive_c/users/steamuser",
            "Bottle/drive_c/users/steamuser/AppData",
            "Bottle/drive_c/users/steamuser/AppData/Roaming",
            "Bottle/drive_c/users/steamuser/AppData/Roaming/TestGame",
            "Bottle/dosdevices",
        ):
            _add_dir(archive, directory)
        _add_bytes(archive, "Bottle/bottle.yml", b"Runner: ge-proton11-1\n")
        if private_registry:
            system_reg = (
                'REGEDIT4\n\n'
                '[Software\\\\Classes\\\\Installer\\\\Products\\\\DEF\\\\SourceList] 123\n'
                '"LastUsedSource"="n;1;Z:\\\\var\\\\home\\\\tester\\\\.cache\\\\installer\\\\"\n'
                '[Software\\\\Classes\\\\Installer\\\\Products\\\\DEF\\\\SourceList\\\\Net] 123\n'
                '"1"=str(2):"Z:\\\\var\\\\home\\\\tester\\\\.cache\\\\installer\\\\"\n'
                '[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Installer\\\\UserData\\\\S-1-5-18\\\\Products\\\\DEF\\\\InstallProperties] 123\n'
                '"InstallSource"="Z:\\\\var\\\\home\\\\tester\\\\.cache\\\\installer\\\\"\n'
                '[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Uninstall\\\\{DEF}] 123\n'
                '"InstallSource"="/home/tester/.cache/installer/"\n'
                '[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Fonts] 123\n'
                '"Tahoma (TrueType)"="Z:\\\\var\\\\home\\\\tester\\\\.local\\\\share\\\\fonts\\\\tahoma.ttf"\n'
            ).encode("utf-8")
        else:
            system_reg = b"REGEDIT4\n"
        _add_bytes(archive, "Bottle/system.reg", system_reg)
        if private_registry:
            user_reg = (
                'REGEDIT4\n\n'
                '[Software\\\\Microsoft\\\\Installer\\\\Products\\\\ABC\\\\SourceList] 123\n'
                '"LastUsedSource"="n;1;Z:\\\\home\\\\tester\\\\.var\\\\app\\\\com.usebottles.bottles\\\\data\\\\bottles\\\\temp\\\\"\n'
                '"PackageName"="/home/tester/.var/app/com.usebottles.bottles/data/bottles/temp/wine_gecko-test-x86_64.msi"\n'
                '[Software\\\\Microsoft\\\\Installer\\\\Products\\\\ABC\\\\SourceList\\\\Net] 123\n'
                '"1"=str(2):"/home/tester/.var/app/com.usebottles.bottles/data/bottles/temp/"\n'
                '[Software\\\\Wine\\\\Fonts\\\\External Fonts] 123\n'
                '"Tahoma (TrueType)"="Z:\\\\var\\\\home\\\\tester\\\\.local\\\\share\\\\fonts\\\\tahoma.ttf"\n'
                + (
                    '[Software\\\\Unknown\\\\Component] 123\n'
                    '"PrivatePath"="Z:\\\\var\\\\home\\\\tester\\\\secret\\\\data.bin"\n'
                    if unknown_private_registry
                    else ""
                )
            ).encode("utf-8")
        else:
            user_reg = b"REGEDIT4\n"
        _add_bytes(archive, "Bottle/user.reg", user_reg)
        _add_bytes(
            archive,
            "Bottle/drive_c/Games/TestGame/TestGame.exe",
            b"MZ-test",
            0o755,
        )
        _add_bytes(
            archive,
            "Bottle/drive_c/Games/TestGame/steam_api64.dll",
            b"dll",
        )
        _add_bytes(
            archive,
            "Bottle/drive_c/Games/TestGame/steam_appid.txt",
            b"123456\n",
        )
        _add_bytes(
            archive,
            "Bottle/drive_c/Games/TestGame/steam_settings/configs.app.ini",
            b"[app::dlcs]\nunlock_all=0\n",
        )
        _add_bytes(
            archive,
            "Bottle/drive_c/users/steamuser/AppData/Roaming/TestGame/save.dat",
            b"save-main",
        )
        if include_backup:
            _add_bytes(
                archive,
                "Bottle/drive_c/users/steamuser/AppData/Roaming/TestGame/save.dat.bak",
                b"save-backup",
            )
        _add_symlink(archive, "Bottle/dosdevices/c:", "../drive_c")
        _add_symlink(archive, "Bottle/dosdevices/z:", "/")


def make_legacy_package(root: Path, *, private_registry: bool = False, unknown_private_registry: bool = False) -> Path:
    for name in (
        "01_JUGAR",
        "02_RUNNER",
        "03_PARCHES_APLICADOS/goldberg_gbe_fork",
        "04_NOTAS_TECNICAS",
        "05_PARTIDAS_GUARDADAS",
        "06_AISLAMIENTO_RED",
        "07_AUDITORIA_SEGURIDAD",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in (
        "00_README.md",
        "FICHA_DEL_JUEGO.md",
        "CREDITOS.md",
        "PRESERVADO_POR.md",
        "MANIFEST_SHA256.txt",
    ):
        (root / name).write_text(name + "\n", encoding="utf-8")

    game = root / "01_JUGAR/backup_TestGame.tar.gz"
    make_bottle_archive(game, private_registry=private_registry, unknown_private_registry=unknown_private_registry)
    (game.with_name(game.name + ".sha256")).write_text(
        f"{sha256_file(game)}  {game.name}\n", encoding="utf-8"
    )

    runner = root / "02_RUNNER/ge-proton11-1.tar.gz"
    with tarfile.open(runner, "w:gz") as archive:
        _add_bytes(archive, "runner/bin/wine", b"wine", 0o755)
    (runner.with_name(runner.name + ".sha256")).write_text(
        f"{sha256_file(runner)}  {runner.name}\n", encoding="utf-8"
    )

    dll = root / "03_PARCHES_APLICADOS/goldberg_gbe_fork/steam_api64.dll"
    dll.write_bytes(b"dll")
    (dll.with_name(dll.name + ".sha256")).write_text(
        f"{sha256_file(dll)}  {dll.name}\n", encoding="utf-8"
    )
    (root / "04_NOTAS_TECNICAS/appmanifest_123456.acf").write_text(
        '"AppState" { "appid" "123456" }\n', encoding="utf-8"
    )
    save_zip = root / "05_PARTIDAS_GUARDADAS/2026-07-24.zip"
    with zipfile.ZipFile(save_zip, "w") as archive:
        archive.writestr("save.dat", b"save-main")
        archive.writestr("save.dat.bak", b"save-backup")
    return root


class ArchiveTests(unittest.TestCase):
    def test_inspection_finds_bottle_and_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "game.tar.gz"
            make_bottle_archive(archive)
            report = inspect_tar(archive)
            self.assertEqual(report["bottle_root_candidates"][0]["path"], "Bottle")
            self.assertEqual(
                report["game_root_candidates"][0]["path"],
                "Bottle/drive_c/Games/TestGame",
            )
            self.assertEqual(report["symlink_count"], 2)
            self.assertEqual(
                report["absolute_symlinks"],
                [{"path": "Bottle/dosdevices/z:", "target": "/"}],
            )

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar"
            with tarfile.open(archive, "w") as handle:
                _add_bytes(handle, "../escape", b"x")
            with self.assertRaises(ImporterError):
                inspect_tar(archive)

    def test_rejects_descendant_below_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.tar"
            with tarfile.open(archive, "w") as handle:
                _add_dir(handle, "root")
                _add_symlink(handle, "root/link", "/")
                _add_bytes(handle, "root/link/escape", b"x")
            with self.assertRaises(ImporterError):
                safe_extract_tar(archive, Path(tmp) / "out")


class TreeReportTests(unittest.TestCase):
    def test_recognizes_legacy_report(self):
        content = """TREE + SHA-256 REPORT
ÁRBOL
=====
[F] 00_README.md
[D] 01_JUGAR
  [F] game.tar.gz
[D] 02_RUNNER
  [F] runner.tar.gz
[D] 03_PARCHES_APLICADOS
[D] 04_NOTAS_TECNICAS
[D] 05_PARTIDAS_GUARDADAS
[D] 06_AISLAMIENTO_RED
[D] 07_AUDITORIA_SEGURIDAD
[F] MANIFEST_SHA256.txt
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  ./01_JUGAR/game.tar.gz
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  ./02_RUNNER/runner.tar.gz
"""
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "tree.log"
            report.write_text(content, encoding="utf-8")
            result = parse_tree_hash_report(report)
            self.assertEqual(result["template"], "legacy-preservation-package-v1")
            self.assertEqual(result["game_archives"][0]["sha256"], "a" * 64)


class EndToEndTests(unittest.TestCase):
    def _plan(self, scan):
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
        plan["runner"]["sha256"] = scan["runner_archives"][0]["computed_sha256"]
        save_root = (
            "drive_c/users/steamuser/AppData/Roaming/TestGame"
        )
        plan["persistent_state"]["items"] = [
            {
                "id": "main-save",
                "path": f"{save_root}/save.dat",
                "kind": "save",
                "disposition": "save-set",
                "save_set_id": "principal",
                "required": False,
                "sensitive": True,
            },
            {
                "id": "main-save-backup",
                "path": f"{save_root}/save.dat.bak",
                "kind": "save",
                "disposition": "save-set",
                "save_set_id": "principal",
                "required": False,
                "sensitive": True,
            },
        ]
        plan["persistent_state"]["unclassified_candidates"] = []
        plan["persistent_state"]["related_file_exceptions"] = []
        return plan

    def test_runner_is_reused_by_vault_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(Path(tmp) / "package")
            runner = root / "02_RUNNER/ge-proton11-1.tar.gz"
            digest = sha256_file(runner)
            vault = Path(tmp) / "vault"
            vault.mkdir()
            write_json(
                vault / "INDEX.json",
                {
                    "schema": 0,
                    "objects": [
                        {
                            "id": "ge-proton11-1",
                            "role": "shared-runner",
                            "sha256": digest,
                        }
                    ],
                },
            )
            scan = scan_package(root, vault=vault, full_hash=False)
            self.assertTrue(scan["runner_archives"][0]["known_in_vault"])
            self.assertEqual(
                scan["runner_archives"][0]["vault_matches"][0]["id"],
                "ge-proton11-1",
            )

    def test_save_zip_is_correlated_with_prefix_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(Path(tmp) / "package")
            scan = scan_package(root, full_hash=True)
            plan = build_plan(scan)
            self.assertEqual(
                set(plan["persistent_state"]["unclassified_candidates"]),
                {
                    "drive_c/users/steamuser/AppData/Roaming/TestGame/save.dat",
                    "drive_c/users/steamuser/AppData/Roaming/TestGame/save.dat.bak",
                },
            )

    def test_prepare_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(Path(tmp) / "package")
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            workspace = Path(tmp) / "workspace"
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            self.assertEqual(receipt["status"], "candidate-prepared")
            self.assertFalse(
                (workspace / "neutral-object/payload/prefix-template/bottle.yml").exists()
            )
            self.assertFalse(
                (
                    workspace
                    / "neutral-object/payload/prefix-template/drive_c/users/steamuser/AppData/Roaming/TestGame/save.dat"
                ).exists()
            )
            self.assertTrue(
                (
                    workspace
                    / "private-state/principal/main-save/save.dat"
                ).is_file()
            )
            result = verify_workspace(workspace)
            self.assertEqual(result["status"], "verified")
            self.assertEqual(
                set(result["state_items_absent_from_neutral_object"]),
                {"main-save", "main-save-backup"},
            )

    def test_prepare_replaces_plan_only_workspace_left_by_gui_020(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(Path(tmp) / "package")
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "IMPORT_PLAN.json").write_text(
                "{}\n", encoding="utf-8"
            )
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            self.assertEqual(receipt["status"], "candidate-prepared")
            self.assertTrue(
                (workspace / "PREPARE_RECEIPT.json").is_file()
            )

    def test_prepare_falls_back_to_archive_path_when_manual_source_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(Path(tmp) / "package")
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            for item in plan["persistent_state"]["items"]:
                item["source_path"] = f"missing-manual-source/{item['id']}"
            workspace = Path(tmp) / "workspace"
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            resolutions = {
                item["id"]: item["source_resolution"]
                for item in receipt["state_items"]
            }
            self.assertEqual(
                resolutions,
                {
                    "main-save": "archive-path-fallback",
                    "main-save-backup": "archive-path-fallback",
                },
            )
            self.assertTrue(
                (
                    workspace
                    / "private-state/principal/main-save/save.dat"
                ).is_file()
            )

    def test_prepare_automatically_sanitizes_known_host_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(
                Path(tmp) / "package",
                private_registry=True,
            )
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            plan["privacy_sanitization"] = {
                "wine_installer_source_lists": [
                    {
                        "id": "wine-gecko-x86_64-source",
                        "file": "payload/prefix-template/user.reg",
                        "key": r"Software\\Microsoft\\Installer\\Products\\ABC\\SourceList",
                        "expected_package_basename": "wine_gecko-test-x86_64.msi",
                        "remove_values": ["LastUsedSource"],
                    }
                ]
            }
            workspace = Path(tmp) / "workspace"
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            self.assertEqual(receipt["status"], "candidate-prepared")
            self.assertTrue(receipt["functional_retest_required"])

            for reg_name in ("system.reg", "user.reg"):
                content = (
                    workspace
                    / f"neutral-object/payload/prefix-template/{reg_name}"
                ).read_text(encoding="utf-8")
                self.assertNotIn("/home/", content)
                self.assertNotIn(r"\\home\\", content)
                self.assertNotIn("LastUsedSource", content)
                self.assertNotIn("InstallSource", content)

            user_reg = (
                workspace
                / "neutral-object/payload/prefix-template/user.reg"
            ).read_text(encoding="utf-8")
            self.assertIn(
                '"PackageName"="wine_gecko-test-x86_64.msi"',
                user_reg,
            )
            self.assertNotIn("Tahoma (TrueType)", user_reg)

            report = json.loads(
                (
                    workspace / "reports/privacy-sanitization.json"
                ).read_text(encoding="utf-8")
            )
            self.assertGreater(report["automatic"]["changes_applied"], 0)
            self.assertEqual(
                report["automatic"]["actions"][
                    "removed-missing-external-font-reference"
                ],
                2,
            )
            self.assertEqual(
                report["declared_results"][0]["status"],
                "already-sanitized",
            )
            privacy = json.loads(
                (
                    workspace / "reports/privacy-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(privacy["blocking_text_hits"], 0)
            self.assertEqual(
                verify_workspace(workspace)["status"],
                "verified",
            )

    def test_sanitize_existing_workspace_and_reseal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(
                Path(tmp) / "package",
                private_registry=True,
            )
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            plan["policy"]["automatic_known_host_path_sanitization"] = False
            workspace = Path(tmp) / "workspace"
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            self.assertEqual(
                receipt["status"],
                "candidate-needs-privacy-review",
            )
            old_digest = receipt["neutral_object"]["sha256"]

            updated = json.loads(json.dumps(plan))
            updated["policy"]["automatic_known_host_path_sanitization"] = True
            result = sanitize_workspace(workspace, updated)
            self.assertEqual(
                result["status"],
                "verified-privacy-clean",
            )
            self.assertNotEqual(
                result["neutral_object_sha256"],
                old_digest,
            )
            privacy = json.loads(
                (
                    workspace / "reports/privacy-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(privacy["blocking_text_hits"], 0)
            final_receipt = json.loads(
                (
                    workspace / "PREPARE_RECEIPT.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                final_receipt["status"],
                "candidate-prepared",
            )
            self.assertTrue(final_receipt["functional_retest_required"])
            self.assertEqual(
                verify_workspace(workspace)["status"],
                "verified",
            )

    def test_unknown_host_path_remains_a_privacy_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(
                Path(tmp) / "package",
                private_registry=True,
                unknown_private_registry=True,
            )
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            workspace = Path(tmp) / "workspace"
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            self.assertEqual(
                receipt["status"],
                "candidate-needs-privacy-review",
            )
            privacy = json.loads(
                (
                    workspace / "reports/privacy-report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(privacy["blocking_text_hits"], 1)
            self.assertEqual(privacy["blocking_files"], 1)

    def test_prepare_records_unclassified_backup_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_legacy_package(Path(tmp) / "package")
            scan = scan_package(root, full_hash=True)
            plan = self._plan(scan)
            plan["persistent_state"]["items"] = [
                plan["persistent_state"]["items"][0]
            ]
            plan["persistent_state"]["require_related_file_closure"] = False
            workspace = Path(tmp) / "workspace"
            receipt = prepare_workspace(
                plan,
                source_package=root,
                workspace=workspace,
            )
            self.assertEqual(receipt["status"], "candidate-prepared")
            closure = json.loads(
                (workspace / "reports/state-closure.json").read_text()
            )
            self.assertEqual(closure["status"], "partial")
            self.assertTrue(
                any(path.endswith("save.dat.bak") for path in closure["unresolved"])
            )


if __name__ == "__main__":
    unittest.main()
