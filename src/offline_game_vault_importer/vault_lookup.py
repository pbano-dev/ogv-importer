from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from .errors import ImporterError
from .util import is_hex_digest


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def known_digests(vault: Path) -> dict[str, list[dict[str, Any]]]:
    index = vault / "INDEX.json"
    if not index.is_file() or index.is_symlink():
        raise ImporterError("el Vault no contiene un INDEX.json regular")
    try:
        document = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImporterError(f"INDEX.json inválido: {exc}") from exc

    found: dict[str, list[dict[str, Any]]] = {}
    for item in _walk(document):
        digest = None
        for key in ("digest", "sha256", "object_sha256"):
            raw = item.get(key)
            if isinstance(raw, str):
                candidate = raw.removeprefix("sha256:").lower()
                if is_hex_digest(candidate):
                    digest = candidate
                    break
        if digest is None:
            continue
        summary: dict[str, Any] = {}
        for key in ("id", "object_id", "role", "roles", "type", "name"):
            if key in item and isinstance(item[key], (str, list)):
                summary[key] = item[key]
        found.setdefault(digest, []).append(summary)
    return found
