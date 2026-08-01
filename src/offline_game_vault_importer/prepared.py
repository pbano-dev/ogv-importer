from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any

from .errors import ImporterError
from .naming import suggest_identifiers
from .util import sha256_file


_EXE_SUFFIXES = {".exe", ".com", ".bat", ".cmd"}
_STEAM_API_NAMES = {"steam_api.dll", "steam_api64.dll"}
_ORIGINAL_HINTS = (
    ".gbe_backup",
    "_original",
    ".original",
    ".bak",
    ".backup",
)


def _regular_directory(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ImporterError("el directorio del juego no es un directorio regular")
    return candidate.resolve(strict=True)


def inspect_prepared_game(
    game: Path,
    *,
    title: str | None = None,
    hash_steam_api: bool = True,
) -> dict[str, Any]:
    """Inspect a user-selected, already decoupled game directory.

    Detection is advisory. The importer never applies Steamless, replaces DLLs,
    downloads Steam components, or claims functional acceptance.
    """
    root = _regular_directory(game)
    executables: list[dict[str, Any]] = []
    steam_api: list[dict[str, Any]] = []
    symlinks: list[dict[str, str]] = []
    file_count = 0
    directory_count = 0
    total_bytes = 0

    for current, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        retained: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                symlinks.append({"path": relative, "target": os.readlink(path)})
            elif stat.S_ISDIR(info.st_mode):
                directory_count += 1
                retained.append(name)
            else:
                raise ImporterError(
                    f"entrada especial no admitida en el juego: {relative}"
                )
        directory_names[:] = retained

        for name in sorted(file_names):
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(info.st_mode):
                symlinks.append({"path": relative, "target": os.readlink(path)})
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ImporterError(
                    f"archivo especial no admitido en el juego: {relative}"
                )
            file_count += 1
            total_bytes += info.st_size
            lower = name.casefold()
            suffix = path.suffix.casefold()
            if suffix in _EXE_SUFFIXES:
                score = 0
                if len(Path(relative).parts) == 1:
                    score += 20
                if "launcher" in lower:
                    score -= 5
                if "protected" in lower or "eac" in lower:
                    score -= 10
                if any(hint in lower for hint in _ORIGINAL_HINTS):
                    score -= 20
                executables.append(
                    {
                        "path": relative,
                        "bytes": info.st_size,
                        "advisory_score": score,
                    }
                )
            if lower in _STEAM_API_NAMES or lower.startswith(
                ("steam_api.dll.", "steam_api64.dll.")
            ):
                item: dict[str, Any] = {
                    "path": relative,
                    "bytes": info.st_size,
                    "active_name": lower in _STEAM_API_NAMES,
                }
                if hash_steam_api:
                    item["sha256"] = sha256_file(path)
                steam_api.append(item)

    executables.sort(
        key=lambda item: (-item["advisory_score"], item["path"].casefold())
    )
    entrypoint_suggestion = None
    if executables:
        best_score = executables[0]["advisory_score"]
        tied = [
            item for item in executables
            if item["advisory_score"] == best_score
        ]
        if len(tied) == 1:
            entrypoint_suggestion = tied[0]["path"]
    return {
        "schema": 0,
        "contract": "ogv-prepared-game-inspection-v1",
        "source_type": "prepared-offline-game-directory-v1",
        "directory_name": root.name,
        "file_count": file_count,
        "directory_count": directory_count,
        "total_bytes": total_bytes,
        "executables": executables,
        "entrypoint_suggestion": entrypoint_suggestion,
        "steam_api_files": sorted(steam_api, key=lambda item: item["path"].casefold()),
        "symlinks": sorted(symlinks, key=lambda item: item["path"].casefold()),
        "naming_suggestions": suggest_identifiers(
            title=title,
            game_directory=root,
        ),
        "limits": [
            "La inspección no demuestra que el juego arranque.",
            "La inspección no demuestra ausencia de DRM de terceros.",
            "La detección de ejecutables es orientativa; la selección del usuario manda.",
        ],
    }
