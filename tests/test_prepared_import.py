from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from offline_game_vault_importer.errors import ImporterError
from offline_game_vault_importer.gui_model import ImportSession
from offline_game_vault_importer.manual_prepare import prepare_prepared_workspace
from offline_game_vault_importer.naming import portable_id, suggest_identifiers
from offline_game_vault_importer.planner import (
    new_prepared_plan,
    public_plan,
    require_prepared_plan_contract,
    validate_plan,
)
from offline_game_vault_importer.prepared import inspect_prepared_game
from offline_game_vault_importer.util import sha256_file
from offline_game_vault_importer.vault_commit import commit_workspace
from offline_game_vault_importer.verify import verify_workspace

from test_commit import FAKE_CORE, make_vault


class NamingTests(unittest.TestCase):
    def test_portable_nomenclature_is_conservative(self):
        self.assertEqual(
            portable_id("NieR Replicant™ ver.1.22474487139…"),
            "nier-replicant-tm-ver-1-22474487139",
        )
        suggestions = suggest_identifiers(title="ELDEN RING NIGHTREIGN")
        self.assertEqual(
            suggestions["capsule_id"],
            "elden-ring-nightreign",
        )
        self.assertEqual(
            suggestions["profiles"]["bottles"],
            "linux-bottles-flatpak",
        )
        self.assertEqual(
            suggestions["state_examples"]["state_id"],
            "elden-ring-nightreign-save",
        )


class PreparedDirectoryTests(unittest.TestCase):
    def test_inspection_proposes_without_claiming_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "Test Game"
            game.mkdir()
            (game / "Game.exe").write_bytes(b"MZ-test")
            (game / "steam_api64.dll").write_bytes(b"gbe")
            inspection = inspect_prepared_game(game)
            self.assertEqual(
                inspection["source_type"],
                "prepared-offline-game-directory-v1",
            )
            self.assertEqual(
                inspection["entrypoint_suggestion"],
                "Game.exe",
            )
            self.assertEqual(
                inspection["naming_suggestions"]["capsule_id"],
                "test-game",
            )
            self.assertEqual(
                inspection["steam_api_files"][0]["sha256"],
                sha256_file(game / "steam_api64.dll"),
            )
            self.assertTrue(
                any("no demuestra" in item for item in inspection["limits"])
            )

    def test_plan_v3_has_one_source_model(self):
        plan = new_prepared_plan(
            title="Test Game",
            game_directory="/tmp/Test Game",
        )
        self.assertEqual(plan["contract"], "ogv-import-plan-v4")
        self.assertEqual(
            plan["source"]["type"],
            "prepared-offline-game-directory-v1",
        )
        self.assertTrue(plan["source"]["steam_independent"])
        self.assertTrue(plan["source"]["preparation"]["performed_before_import"])
        self.assertEqual(
            [p["adapter"] for p in plan["profiles"] if p["enabled"]],
            ["bottles"],
        )
        public = public_plan(plan)
        self.assertIsNone(public["source"]["game_directory"])
        with self.assertRaises(ImporterError):
            require_prepared_plan_contract(
                {
                    "contract": "ogv-import-plan-v2",
                    "source": {"type": "legacy-preservation-package-v1"},
                }
            )


class PreparedEndToEndTests(unittest.TestCase):
    def test_prepared_game_without_state_skips_state_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / "Prepared Game"
            game.mkdir()
            (game / "Game.exe").write_bytes(b"MZ-prepared")

            fake = root / "fake_core.py"
            fake.write_text(FAKE_CORE, encoding="utf-8")

            plan = new_prepared_plan(
                title="Prepared Game",
                game_directory=str(game),
            )
            plan["status"] = "confirmed"
            plan["identity"].update(
                {
                    "preserved_version": "1.0",
                    "capsule_id": "prepared-game",
                }
            )
            plan["layout"].update(
                {
                    "entrypoint": "Game.exe",
                    "game_destination_in_prefix": "drive_c/Games/prepared-game",
                    "working_directory": "drive_c/Games/prepared-game",
                }
            )
            plan["core"]["command"] = f"{sys.executable} -S {fake}"

            workspace = root / "workspace"
            prepare_prepared_workspace(
                plan,
                game=game,
                prefix=None,
                workspace=workspace,
            )
            vault = make_vault(root / "vault")
            result = commit_workspace(workspace, vault=vault, dry_run=True)

            self.assertEqual(result["status"], "dry-run-valid")
            self.assertEqual(result["state"]["backup_status"], "not-applicable")
            self.assertIsNone(result["state"]["accepted_backup_id"])
            self.assertIsNone(result["state"]["accepted_verified"])
            self.assertEqual(result["state"]["save_set_count"], 0)


    def test_prepared_game_imports_state_extras_and_selected_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / "inputs"
            game = inputs / "My Prepared Game"
            game.mkdir(parents=True)
            (game / "MyGame.exe").write_bytes(b"MZ-prepared")
            (game / "steam_api64.dll").write_bytes(b"gbe-fork")
            (game / "steam_api64.dll.gbe_backup").write_bytes(b"steam-original")

            save = inputs / "save.dat"
            save.write_bytes(b"save-data")
            artbook = inputs / "artbook"
            artbook.mkdir()
            (artbook / "page.txt").write_text("art", encoding="utf-8")
            readme = inputs / "README-custom.md"
            readme.write_text(
                "# Documento seleccionado\n\nContenido exacto.\n",
                encoding="utf-8",
            )

            fake = root / "fake_core.py"
            fake.write_text(FAKE_CORE, encoding="utf-8")

            plan = new_prepared_plan(
                title="My Prepared Game",
                game_directory=str(game),
            )
            plan["status"] = "confirmed"
            plan["identity"].update(
                {
                    "edition": "Standard",
                    "appid": "123456",
                    "preserved_version": "1.2.3",
                    "capsule_id": "my-prepared-game",
                }
            )
            plan["layout"].update(
                {
                    "entrypoint": "MyGame.exe",
                    "game_destination_in_prefix": (
                        "drive_c/Games/my-prepared-game"
                    ),
                    "working_directory": "drive_c/Games/my-prepared-game",
                }
            )
            plan["core"]["command"] = f"{sys.executable} -S {fake}"
            session = ImportSession(plan=plan)
            session.add_state(
                state_id="main-save",
                source_path=str(save),
                destination_path=(
                    "drive_c/users/steamuser/AppData/Roaming/"
                    "MyPreparedGame/save.dat"
                ),
                disposition="save-set",
                kind="save",
                save_set_id="main",
                display_name="Principal",
            )
            session.add_supplemental(
                item_id="digital-artbook",
                source_path=str(artbook),
                classification="artbook",
                description="Artbook poseído.",
            )
            session.add_documentation(
                item_id="custom-readme",
                source_path=str(readme),
                role="readme",
                canonical_name="00_README.md",
            )
            validate_plan(plan, phase="commit")

            workspace = root / "workspace"
            prepare_prepared_workspace(
                plan,
                game=game,
                prefix=None,
                workspace=workspace,
            )
            verification = verify_workspace(workspace)
            self.assertEqual(verification["selected_components_verified"], 3)
            self.assertTrue(verification["public_plan_verified"])

            copied_artbook_page = (
                workspace
                / "selected-components/supplemental_content"
                / "digital-artbook/artbook/page.txt"
            )
            copied_artbook_page.write_text("alterado", encoding="utf-8")
            with self.assertRaises(ImporterError):
                verify_workspace(workspace)
            copied_artbook_page.write_text("art", encoding="utf-8")
            verify_workspace(workspace)

            workspace_plan = json.loads(
                (workspace / "IMPORT_PLAN.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                workspace_plan["documentation"][0]["workspace_path"].startswith(
                    "selected-components/documentation/"
                )
            )
            public = json.loads(
                (workspace / "PUBLIC_IMPORT_PLAN.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(public["source"]["game_directory"])
            self.assertIsNone(
                public["documentation"][0]["source_path"]
            )
            self.assertIsNone(
                public["supplemental_content"][0]["source_path"]
            )
            self.assertIsNone(
                public["persistent_state"]["items"][0]["source_path"]
            )

            # Commit must depend on the prepared workspace, not on the original
            # manually selected documentation, state, or supplemental paths.
            shutil.rmtree(inputs)

            vault = make_vault(root / "vault")
            result = commit_workspace(workspace, vault=vault)
            self.assertEqual(result["status"], "candidate-imported")
            capsule = vault / "02_CAPSULES/my-prepared-game"
            self.assertEqual(
                (capsule / "docs/00_README.md").read_text(encoding="utf-8"),
                "# Documento seleccionado\n\nContenido exacto.\n",
            )
            self.assertTrue(
                (capsule / "supplemental-content/digital-artbook/"
                 "artbook/page.txt").is_file()
            )
            public_evidence = json.loads(
                (capsule / "evidence/IMPORT_PLAN.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIsNone(
                public_evidence["source"]["game_directory"]
            )
            self.assertEqual(
                result["documentation"][0]["status"],
                "selected-and-hashed",
            )


if __name__ == "__main__":
    unittest.main()
