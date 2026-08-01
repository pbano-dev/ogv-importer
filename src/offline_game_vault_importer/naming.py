from __future__ import annotations

from pathlib import Path
import re
import unicodedata
from typing import Any


_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


def portable_id(value: str, *, fallback: str = "game") -> str:
    """Convert user-visible text into a conservative portable identifier."""
    prepared = (
        value.replace("™", " tm ")
        .replace("®", " r ")
        .replace("©", " c ")
    )
    normalized = unicodedata.normalize("NFKD", prepared)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or fallback


def is_portable_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))


def canonical_profile_id(adapter: str) -> str:
    values = {
        "bottles": "linux-bottles-flatpak",
        "wine": "linux-direct-wine",
        "umu": "linux-umu-proton",
        "windows": "windows-native",
    }
    return values.get(adapter, portable_id(adapter, fallback="profile"))


def suggest_identifiers(
    *,
    title: str | None = None,
    game_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Return editable nomenclature suggestions without treating them as evidence."""
    directory_name = ""
    if game_directory:
        directory_name = Path(game_directory).expanduser().name
    basis = (title or "").strip() or directory_name.strip() or "game"
    capsule_id = portable_id(basis)
    return {
        "basis": basis,
        "capsule_id": capsule_id,
        "game_destination_in_prefix": f"drive_c/Games/{capsule_id}",
        "profiles": {
            adapter: canonical_profile_id(adapter)
            for adapter in ("bottles", "wine", "umu", "windows")
        },
        "state_examples": {
            "save_set_id": f"{capsule_id}-main",
            "state_id": f"{capsule_id}-save",
        },
        "supplemental_examples": {
            "artbook": f"{capsule_id}-artbook",
            "soundtrack": f"{capsule_id}-soundtrack",
            "manual": f"{capsule_id}-manual",
        },
        "documentation_examples": {
            "readme": "00_README.md",
            "game_sheet": "FICHA_DEL_JUEGO.md",
            "credits": "CREDITOS.md",
            "preserved_by": "PRESERVADO_POR.md",
            "technical_notes": "NOTAS_TECNICAS_DEL_PROCESO.md",
        },
    }
