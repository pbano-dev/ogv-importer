from __future__ import annotations

import ast
import unittest
from pathlib import Path

import offline_game_vault_importer.vault_commit as vault_commit


def _is_format_reference(
    node: ast.AST,
    *,
    object_name: str,
) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == object_name
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == "format"
    )


class ManifestIngestContractTests(unittest.TestCase):
    def test_every_new_object_ingest_passes_declared_format(self) -> None:
        path = Path(vault_commit.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))

        found: dict[str, ast.AST] = {}
        for call in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_core_json"
            and node.args
            and isinstance(node.args[0], ast.List)
        ):
            values = call.args[0].elts
            if (
                not values
                or not isinstance(values[0], ast.Constant)
                or values[0].value != "ingest-object"
            ):
                continue

            source_name = None
            format_node = None
            for index, value in enumerate(values[:-1]):
                if isinstance(value, ast.Constant) and value.value == "--source":
                    expression = values[index + 1]
                    if (
                        isinstance(expression, ast.Call)
                        and isinstance(expression.func, ast.Name)
                        and expression.func.id == "str"
                        and len(expression.args) == 1
                        and isinstance(expression.args[0], ast.Name)
                    ):
                        source_name = expression.args[0].id
                if isinstance(value, ast.Constant) and value.value == "--format":
                    format_node = values[index + 1]

            if source_name is not None and format_node is not None:
                found[source_name] = format_node

        self.assertEqual(set(found), {"game_source", "runner_source"})
        self.assertTrue(
            _is_format_reference(
                found["game_source"],
                object_name="game_object",
            )
        )
        self.assertTrue(
            _is_format_reference(
                found["runner_source"],
                object_name="runner_object",
            )
        )


if __name__ == "__main__":
    unittest.main()
