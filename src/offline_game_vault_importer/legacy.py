from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .archive import inspect_tar
from .errors import ImporterError
from .save_archive import inspect_save_zip
from .util import regular_dir, sha256_file
from .vault_lookup import known_digests

_REQUIRED = (
    "00_README.md",
    "01_JUGAR",
    "02_RUNNER",
    "03_PARCHES_APLICADOS",
    "04_NOTAS_TECNICAS",
    "05_PARTIDAS_GUARDADAS",
    "06_AISLAMIENTO_RED",
    "07_AUDITORIA_SEGURIDAD",
    "MANIFEST_SHA256.txt",
)


def _sidecar_digest(path: Path) -> str | None:
    candidates = [
        path.with_name(path.name + ".sha256"),
        path.with_suffix(path.suffix + ".sha256"),
    ]
    for sidecar in candidates:
        if not sidecar.is_file() or sidecar.is_symlink():
            continue
        try:
            text = sidecar.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        match = re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text.lower())
        if match:
            return match.group(0)
    return None


def _archives(directory: Path, suffixes: tuple[str, ...]) -> list[Path]:
    result: list[Path] = []
    if not directory.is_dir():
        return result
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if path.is_symlink() or not path.is_file():
            continue
        lower = path.name.lower()
        if lower.endswith(".sha256"):
            continue
        if lower.endswith(suffixes):
            result.append(path)
    return result


def scan_package(
    source: Path,
    *,
    vault: Path | None = None,
    inspect_game_archive: bool = True,
    full_hash: bool = False,
) -> dict[str, Any]:
    root = regular_dir(source, "paquete fuente")
    missing = [name for name in _REQUIRED if not (root / name).exists()]
    if missing:
        raise ImporterError(
            "el paquete no cumple legacy-preservation-package-v1; faltan: "
            + ", ".join(missing)
        )

    game_archives = _archives(root / "01_JUGAR", (".tar.gz", ".tgz", ".tar"))
    if len(game_archives) != 1:
        raise ImporterError(
            "se requiere exactamente un archivo jugable en 01_JUGAR; "
            f"encontrados: {len(game_archives)}"
        )
    runners = _archives(
        root / "02_RUNNER", (".tar.gz", ".tgz", ".tar", ".tar.zst")
    )
    saves = _archives(root / "05_PARTIDAS_GUARDADAS", (".zip", ".tar.gz"))

    known = known_digests(vault) if vault is not None else {}

    save_entries: list[dict[str, Any]] = []
    state_hints: list[dict[str, Any]] = []
    for path in saves:
        sidecar = _sidecar_digest(path)
        inspection = (
            inspect_save_zip(path, full_hash=full_hash)
            if path.name.lower().endswith(".zip")
            else None
        )
        computed = (
            inspection.get("archive_sha256")
            if isinstance(inspection, dict)
            else (sha256_file(path) if full_hash else None)
        )
        if sidecar and computed and sidecar != computed:
            raise ImporterError(
                f"hash lateral no coincide para {path.name}: "
                f"{sidecar} != {computed}"
            )
        relative = path.relative_to(root).as_posix()
        save_entries.append(
            {
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "declared_sha256": sidecar,
                "computed_sha256": computed,
                "full_hash_verified": bool(computed),
                "archive_inspection": inspection,
            }
        )
        if isinstance(inspection, dict):
            for member in inspection.get("members", []):
                if member.get("kind") != "file":
                    continue
                state_hints.append(
                    {
                        "source_archive": relative,
                        "source_member": member.get("path"),
                        "basename": member.get("basename"),
                        "size": member.get("size"),
                        "sha256": member.get("sha256"),
                    }
                )

    game_entries = []
    for path in game_archives:
        sidecar = _sidecar_digest(path)
        computed = sha256_file(path) if full_hash else None
        if sidecar and computed and sidecar != computed:
            raise ImporterError(
                f"hash lateral no coincide para {path.name}: "
                f"{sidecar} != {computed}"
            )
        game_entries.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "declared_sha256": sidecar,
                "computed_sha256": computed,
                "full_hash_verified": bool(computed),
                "archive_inspection": (
                    inspect_tar(
                        path,
                        full_hash=False,
                        match_hints=state_hints,
                    )
                    if inspect_game_archive else None
                ),
            }
        )

    runner_entries = []
    for path in runners:
        sidecar = _sidecar_digest(path)
        computed = sha256_file(path) if full_hash else None
        digest = computed or sidecar
        runner_entries.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "declared_sha256": sidecar,
                "computed_sha256": computed,
                "full_hash_verified": bool(computed),
                "known_in_vault": bool(digest and digest in known),
                "vault_matches": known.get(digest, []) if digest else [],
            }
        )

    api_candidates = []
    for path in sorted((root / "03_PARCHES_APLICADOS").rglob("steam_api*.dll")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if "/GBE-Standalone/" in "/" + relative:
            continue
        digest = sha256_file(path) if full_hash else _sidecar_digest(path)
        api_candidates.append(
            {
                "relative_path": relative,
                "bytes": path.stat().st_size,
                "sha256": digest,
                "known_in_vault": bool(digest and digest in known),
                "vault_matches": known.get(digest, []) if digest else [],
            }
        )

    appmanifests = [
        path.relative_to(root).as_posix()
        for path in sorted((root / "04_NOTAS_TECNICAS").glob("appmanifest_*.acf"))
        if path.is_file() and not path.is_symlink()
    ]

    return {
        "schema": 0,
        "tool": "offline-game-vault-importer",
        "tool_version": "0.1.0",
        "source_type": "legacy-preservation-package-v1",
        "status": "scanned",
        # Deliberately no absolute source path.
        "source_name": root.name,
        "full_hash_verified": full_hash,
        "game_archives": game_entries,
        "runner_archives": runner_entries,
        "save_archives": save_entries,
        "active_steam_api_candidates": api_candidates,
        "appmanifests": appmanifests,
        "documents": [
            name for name in (
                "00_README.md",
                "FICHA_DEL_JUEGO.md",
                "CREDITOS.md",
                "PRESERVADO_POR.md",
            )
            if (root / name).is_file()
        ],
        "modules": {
            "steamless": (root / "03_PARCHES_APLICADOS/steamless").is_dir(),
            "eac_launcher_omission": (
                root / "03_PARCHES_APLICADOS/omision_eac"
            ).is_dir(),
            "input": (root / "09_INPUT_Y_MANDO").is_dir(),
            "supplemental_content": (
                root / "08_CONTENIDO_ADICIONAL"
            ).is_dir(),
        },
    }
