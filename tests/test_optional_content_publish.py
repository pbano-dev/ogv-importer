from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from offline_game_vault_importer.errors import ImporterError
from offline_game_vault_importer.vault_commit import (
    _prepare_optional_content,
)


class OptionalContentPublishTests(unittest.TestCase):
    def _prepare(
        self,
        *,
        source: Path,
        item: dict,
    ):
        workspace = source.parent
        plan = {
            "supplemental_content": [
                {
                    "id": "bonus",
                    "source_path": str(source),
                    "classification": "dlc",
                    "description": "Bonus content",
                    **item,
                }
            ]
        }
        temporary = workspace / "optional-stage"
        return _prepare_optional_content(
            workspace=workspace,
            plan=plan,
            temporary_root=temporary,
            reserved_object_ids={"game-baseline"},
        )

    def test_old_plan_defaults_to_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "Bonus"
            source.mkdir()
            (source / "manual.pdf").write_bytes(b"manual")

            prepared, inventory = self._prepare(
                source=source,
                item={},
            )

            self.assertEqual(len(prepared), 1)
            declaration = prepared[0]["optional_content"]
            self.assertEqual(
                declaration["placement"],
                {"mode": "sidecar", "destination": "bonus"},
            )
            self.assertEqual(
                prepared[0]["object"]["roles"],
                ["supplemental_content"],
            )
            self.assertFalse(prepared[0]["object"]["required"])
            self.assertEqual(
                inventory[0]["status"],
                "prepared-immutable-object",
            )

    def test_explicit_game_overlay_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "DLC"
            source.mkdir()
            (source / "content.bin").write_bytes(b"dlc")

            prepared, _inventory = self._prepare(
                source=source,
                item={
                    "placement": {
                        "mode": "game-overlay",
                        "destination": "DLC",
                    }
                },
            )
            self.assertEqual(
                prepared[0]["optional_content"]["placement"],
                {"mode": "game-overlay", "destination": "DLC"},
            )

    def test_regular_file_is_wrapped_as_directory_subtree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "manual.pdf"
            source.write_bytes(b"manual")

            prepared, _inventory = self._prepare(
                source=source,
                item={},
            )
            self.assertEqual(
                prepared[0]["optional_content"]["source"],
                "bonus",
            )
            self.assertTrue(prepared[0]["source_path"].is_file())
            self.assertEqual(
                prepared[0]["object"]["format"],
                "tar.gz",
            )

    def test_missing_source_remains_pending_not_operational(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "absent"

            prepared, inventory = self._prepare(
                source=source,
                item={},
            )
            self.assertEqual(prepared, [])
            self.assertEqual(inventory[0]["status"], "pending")
            self.assertFalse(inventory[0]["source_present"])

    def test_nested_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "Bonus"
            source.mkdir()
            target = source / "real.txt"
            target.write_bytes(b"x")
            link = source / "link.txt"
            try:
                link.symlink_to("real.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")

            with self.assertRaises(ImporterError):
                self._prepare(source=source, item={})

    def test_unsafe_placement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "Bonus"
            source.mkdir()
            (source / "x").write_bytes(b"x")

            with self.assertRaises(ImporterError):
                self._prepare(
                    source=source,
                    item={
                        "placement": {
                            "mode": "game-overlay",
                            "destination": "../escape",
                        }
                    },
                )


if __name__ == "__main__":
    unittest.main()
