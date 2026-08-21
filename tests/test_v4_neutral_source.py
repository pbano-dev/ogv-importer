from __future__ import annotations

import unittest
from copy import deepcopy

from offline_game_vault_importer.planner import new_prepared_plan, upgrade_plan
from offline_game_vault_importer.vault_commit import (
    _canonical_game_source_contract,
    _capsule_document,
)


class V4NeutralSourceTests(unittest.TestCase):
    def plan(self) -> dict:
        plan = new_prepared_plan(
            title="Neutral Test Game",
            game_directory="/games/Neutral Test Game",
        )
        plan["identity"]["preserved_version"] = "1"
        plan["layout"]["entrypoint"] = "Game.exe"
        return plan

    def test_new_plan_has_one_source_model(self) -> None:
        plan = self.plan()
        self.assertEqual(plan["contract"], "ogv-import-plan-v4")
        self.assertEqual(len(plan["profiles"]), 1)
        self.assertEqual(plan["profiles"][0]["id"], "game-source")

    def test_v3_upgrade_is_readable_as_v4(self) -> None:
        legacy = self.plan()
        legacy["contract"] = "ogv-import-plan-v3"
        upgraded = upgrade_plan(legacy)
        self.assertEqual(upgraded["contract"], "ogv-import-plan-v4")

    def test_capsule_source_is_backend_neutral_and_runner_unbound(self) -> None:
        plan = self.plan()
        game_object = {
            "id": "game-baseline",
            "digest": "sha256:" + "1" * 64,
            "roles": ["game_payload"],
            "format": "tar.gz",
            "required": True,
            "archive_path": "objects/sha256/11/11/" + "1" * 64,
            "shared": False,
            "size": 1,
        }
        runner_object = {
            "id": "proton-test",
            "digest": "sha256:" + "2" * 64,
            "roles": ["runner"],
            "format": "tar.gz",
            "required": True,
            "archive_path": "objects/sha256/22/22/" + "2" * 64,
            "shared": True,
            "size": 1,
        }
        capsule = _capsule_document(
            plan=plan,
            game_object=game_object,
            runner_object=runner_object,
        )
        self.assertEqual(len(capsule["profiles"]), 1)
        profile = capsule["profiles"][0]
        self.assertEqual(profile["id"], "game-source")
        self.assertEqual(profile["adapter"], "other")
        self.assertEqual(
            profile["host_contract"],
            "host-contracts/game-source.json",
        )
        self.assertEqual(profile["dependencies"], ["game-baseline"])

    def test_canonical_contract_drops_backend_and_runner_binding(self) -> None:
        legacy = {
            "schema": 0,
            "contract": "ogv-direct-wine-neutral-v1",
            "backend": "direct-wine",
            "adapter": "wine",
            "runner_binding": "preferred",
            "preferred_runner": {"object_id": "runner"},
        }
        result = _canonical_game_source_contract(deepcopy(legacy))
        self.assertEqual(result["contract"], "ogv-game-source-v1")
        self.assertEqual(
            result["runner_binding"],
            "select-at-materialization",
        )
        self.assertIsNone(result["preferred_runner"])
        self.assertNotIn("backend", result)
        self.assertNotIn("adapter", result)

    def test_default_destination_is_topology_not_source_root(self) -> None:
        plan = self.plan()
        self.assertEqual(
            plan["layout"]["game_destination_in_prefix"],
            "drive_c/Games/neutral-test-game",
        )
        self.assertEqual(
            plan["source"]["game_directory"],
            "/games/Neutral Test Game",
        )


if __name__ == "__main__":
    unittest.main()
