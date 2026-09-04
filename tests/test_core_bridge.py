from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

from offline_game_vault_importer.core_bridge import (
    MINIMUM_CORE,
    REQUIRED_COMMANDS,
    probe_core,
)
from offline_game_vault_importer.errors import ImporterError


FAKE_CORE = r"""
from __future__ import annotations
import sys

version = sys.argv[1]
arguments = sys.argv[2:]
if arguments == ["--version"]:
    print(f"offline-game-vault {version}")
    raise SystemExit(0)
if len(arguments) == 2 and arguments[1] == "--help":
    if arguments[0] == "compose" and version.endswith("-missing-compose"):
        print("unsupported compose", file=sys.stderr)
        raise SystemExit(2)
    print(f"usage: ogv {arguments[0]}")
    raise SystemExit(0)
print("unsupported invocation", file=sys.stderr)
raise SystemExit(2)
"""


class CoreBridgeTests(unittest.TestCase):
    def _plan(self, root: Path, version: str) -> dict[str, object]:
        fake = root / "fake_core.py"
        fake.write_text(FAKE_CORE, encoding="utf-8")
        return {
            "core": {
                "command": f"{sys.executable} -S {fake} {version}",
            }
        }

    def test_accepts_current_complete_core_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            probe = probe_core(self._plan(Path(temporary), "0.19.7"))

        self.assertEqual(probe.version_tuple, MINIMUM_CORE)
        self.assertEqual(probe.commands, REQUIRED_COMMANDS)
        self.assertEqual(probe.to_dict()["minimum_version"], "0.19.7")

    def test_rejects_outdated_core_before_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ImporterError, "demasiado antiguo"):
                probe_core(self._plan(Path(temporary), "0.19.6"))

    def test_rejects_missing_public_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ImporterError, "compose"):
                probe_core(
                    self._plan(Path(temporary), "0.19.7-missing-compose")
                )


if __name__ == "__main__":
    unittest.main()
