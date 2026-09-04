from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from offline_game_vault_importer.core_bridge import probe_core, run_core_json
from offline_game_vault_importer.gui_model import ImportSession

from test_commit import make_vault


CORE_SOURCE_ROOT = os.environ.get("OGV_CORE_SOURCE_ROOT")
GUI_SOURCE_ROOT = os.environ.get("OGV_GUI_SOURCE_ROOT")


@unittest.skipUnless(
    CORE_SOURCE_ROOT and GUI_SOURCE_ROOT,
    "set OGV_CORE_SOURCE_ROOT and OGV_GUI_SOURCE_ROOT to run integration",
)
class CurrentCoreContractTests(unittest.TestCase):
    def test_imported_capsule_and_optional_content_are_core_readable(self) -> None:
        assert CORE_SOURCE_ROOT is not None
        assert GUI_SOURCE_ROOT is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "Kingdom Hearts"
            game.mkdir()
            (game / "KINGDOM HEARTS.exe").write_bytes(b"MZ-game")
            artbook = root / "artbook"
            artbook.mkdir()
            (artbook / "page.png").write_bytes(b"image")
            runner = root / "local-wine"
            (runner / "files/bin").mkdir(parents=True)
            for executable in ("wine", "wineserver"):
                path = runner / "files/bin" / executable
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)

            session = ImportSession()
            plan = session.new_prepared(game=game, title="Kingdom Hearts")
            plan["core"]["source_root"] = CORE_SOURCE_ROOT
            session.set_runner(
                binding="select-at-materialization",
                source_path=str(runner),
                preferred_id="local-wine",
                digest=None,
            )
            session.add_supplemental(
                item_id="digital-artbook",
                source_path=str(artbook),
                classification="artbook",
            )
            session.vault = make_vault(root / "vault")

            imported = session.import_prepared_game(
                game=game,
                prefix=None,
                workspace=root / "workspace",
            )
            capsule_path = (
                session.vault
                / "02_CAPSULES/kingdom-hearts/capsule.json"
            )
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
            core = probe_core(plan)
            audit = run_core_json(
                ["audit-capsule", "--capsule", str(capsule_path)],
                plan=plan,
            )
            optional = run_core_json(
                [
                    "list-optional-content",
                    "--collection-root",
                    str(session.vault),
                    "--capsule",
                    str(capsule_path),
                ],
                plan=plan,
            )
            materialized_path = root / "materialized"
            composition = run_core_json(
                [
                    "compose",
                    "--collection-root",
                    str(session.vault),
                    "--capsule",
                    str(capsule_path),
                    "--backend",
                    "direct-wine",
                    "--runner",
                    "local-wine",
                    "--destination",
                    str(materialized_path),
                    "--content-id",
                    "digital-artbook",
                ],
                plan=plan,
            )
            gui_source = str(Path(GUI_SOURCE_ROOT).resolve() / "src")
            sys.path.insert(0, gui_source)
            try:
                from offline_game_vault_gui.catalog import CapsuleCatalog

                gui_games = CapsuleCatalog().scan(session.vault)
            finally:
                sys.path.remove(gui_source)

            self.assertGreaterEqual(core.version_tuple, (0, 19, 7))
            self.assertEqual(imported["status"], "candidate-imported")
            self.assertTrue(audit["valid"])
            self.assertEqual(capsule["profiles"][0]["id"], "game-source")
            self.assertEqual(
                [item["id"] for item in optional["items"]],
                ["digital-artbook"],
            )
            self.assertTrue(optional["items"][0]["available"])
            self.assertTrue(composition["materialized"])
            self.assertEqual(composition["backend"], "direct-wine")
            self.assertEqual(composition["runner_id"], "local-wine")
            self.assertTrue(
                (materialized_path / "extras/digital-artbook/page.png").is_file()
            )
            self.assertEqual(len(gui_games), 1)
            self.assertEqual(gui_games[0].capsule_id, "kingdom-hearts")
            self.assertEqual(
                gui_games[0].source_profiles[0].profile_id,
                "game-source",
            )
            self.assertEqual(gui_games[0].source_profiles[0].adapter, "other")


if __name__ == "__main__":
    unittest.main()
