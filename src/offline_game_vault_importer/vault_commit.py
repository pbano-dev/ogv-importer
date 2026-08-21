from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import tempfile
import tarfile
import gzip
from typing import Any, Iterator
import uuid

from . import __version__
from .core_bridge import core_version, run_core_json
from .errors import ImporterError
from .planner import (
    public_plan,
    require_prepared_plan_contract,
    validate_plan,
)
from .util import canonical_json_bytes, read_json, sha256_file, write_json
from .verify import verify_workspace


_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CRITICAL = (
    "INDEX.json",
    "COLLECTION_LAYOUT.json",
    "COLLECTION_SHA256.txt",
    "01_IMMUTABLE_VAULT/VAULT_INVENTORY.json",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _operation_id(prefix: str = "import-game-via-importer-v3") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{secrets.token_hex(4)}"


def _safe_relative(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ImporterError(f"{label} no es una ruta relativa portable")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ImporterError(f"{label} no es una ruta relativa segura")
    return path


def _regular_root(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ImporterError(f"{label} no es un directorio regular")
    return candidate.resolve(strict=True)


def _load_required_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ImporterError(f"{label} no es un fichero regular")
    return read_json(path, label)


def _object_rel(digest_hex: str) -> str:
    if not _DIGEST_RE.fullmatch(digest_hex):
        raise ImporterError("digest de objeto no es SHA-256 hexadecimal")
    return (
        f"objects/sha256/{digest_hex[:2]}/{digest_hex[2:4]}/{digest_hex}"
    )


def _slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return text or fallback


def _archive_format(path: Path) -> str:
    name = path.name.casefold()
    if name.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if name.endswith(".tar.zst"):
        return "tar.zst"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    return "file"


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _state_definition_digest(declaration: dict[str, Any]) -> str:
    normalized = {
        "id": declaration.get("id"),
        "path": declaration.get("path"),
        "kind": declaration.get("kind"),
        "backup": declaration.get("backup", True),
        "sensitive": declaration.get("sensitive", False),
        "required": declaration.get("required", True),
    }
    return _canonical_digest(normalized)


def _aggregate_save_digest(items: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "state_id": item["state_id"],
            "declared_path": item["declared_path"],
            "digest": item["digest"],
            "bytes": item["bytes"],
            "entry_type": item["entry_type"],
        }
        for item in items
    ]
    return _canonical_digest(normalized)


def _validate_plain_tree(source: Path, label: str) -> None:
    """Reject links and special files before copying a user-selected tree.

    State and supplemental payloads are published as plain data. Following a
    nested symlink here could silently ingest bytes outside the user's
    selection, so such trees must be normalized explicitly before import.
    """
    for current, directory_names, file_names in os.walk(
        source, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        retained: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                raise ImporterError(
                    f"{label}: symlink anidado no admitido: {relative}"
                )
            if not stat.S_ISDIR(info.st_mode):
                raise ImporterError(
                    f"{label}: entrada especial no admitida: {relative}"
                )
            retained.append(name)
        directory_names[:] = retained
        for name in sorted(file_names):
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                raise ImporterError(
                    f"{label}: symlink anidado no admitido: {relative}"
                )
            if not stat.S_ISREG(info.st_mode):
                raise ImporterError(
                    f"{label}: archivo especial no admitido: {relative}"
                )


def _copy_regular_or_tree(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise ImporterError(f"no se admite una fuente symlink: {source}")
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return
    if source.is_dir():
        _validate_plain_tree(source, str(source))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=False)
        return
    raise ImporterError(f"fuente de estado no regular: {source}")


def _source_for_item(workspace: Path, item: dict[str, Any]) -> Path | None:
    raw = item.get("workspace_path")
    if isinstance(raw, str) and raw:
        rel = _safe_relative(raw, f"{item.get('id')}.workspace_path")
        candidate = workspace.joinpath(*rel.parts)
        if candidate.exists() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    raw = item.get("source_path")
    if isinstance(raw, str) and raw:
        candidate = Path(raw).expanduser()
        if candidate.exists() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    return None


def _tree_entries(root: Path) -> tuple[list[dict[str, Any]], int, int, int]:
    if root.is_file():
        info = root.stat()
        item = {
            "path": ".",
            "type": "file",
            "mode": stat.S_IMODE(info.st_mode),
            "bytes": info.st_size,
            "digest": "sha256:" + sha256_file(root),
        }
        return [item], 1, 0, info.st_size
    entries: list[dict[str, Any]] = []
    files = directories = total = 0
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ImporterError(f"el estado no admite symlinks: {path}")
        relative = path.relative_to(root).as_posix()
        info = path.stat()
        if path.is_dir():
            directories += 1
            entries.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(info.st_mode),
                }
            )
        elif path.is_file():
            files += 1
            total += info.st_size
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "bytes": info.st_size,
                    "digest": "sha256:" + sha256_file(path),
                }
            )
        else:
            raise ImporterError(f"tipo de estado no admitido: {path}")
    return entries, files, directories, total


def _write_templates(capsule_root: Path, identity: dict[str, Any]) -> None:
    docs = capsule_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    title = identity["title"]
    version = identity["preserved_version"]
    appid = identity.get("appid", "[RELLENAR]")
    edition = identity.get("edition") or "[RELLENAR]"
    readme = f"""# {title} — cápsula candidata

**Edición fuente:** {edition}
**AppID:** {appid}
**Versión preservada:** {version}
**Estado:** CANDIDATO IMPORTADO

Esta cápsula fue creada por OfflineGameVault Importer. La importación acredita
integridad estructural, no aceptación funcional.

## Uso

Seleccione un perfil, runner y partida desde OfflineGameVault GUI. Los perfiles
se mantienen como `candidate` o `not_tested` hasta completar sus pruebas.

## Límites

- Arranque: [NO PROBADO]
- Partidas: [NO PROBADO]
- DLC: [VERIFICAR]
- Aislamiento de red: [NO PROBADO]
- Windows nativo: [NO PROBADO]
"""
    game_sheet = f"""# FICHA DEL JUEGO — {title}

## Identificación

- Tienda fuente: {identity["source_store"]}
- AppID: {appid}
- Edición: {edition}
- Versión preservada: {version}

## Obra y desarrollo

[RELLENAR CON FUENTES PRIMARIAS]

## Contenido preservado

Consultar `capsule.json`, `CONTENT_STATUS.json` y los recibos de importación.
"""
    credits = f"""# CRÉDITOS — {title}

Los créditos completos permanecen en el juego.

## Roles principales

[RELLENAR DESDE CRÉDITOS IN-GAME]
"""
    preserved = """# Preservado por

Este documento firma el paquete de preservación, no la autoría del juego.

## Autoría del paquete

[RELLENAR]

## Operaciones

- Importación estructural mediante OfflineGameVault Importer 0.3.0.
- Separación declarada de estado persistente.
- Hashes SHA-256 y recibo transaccional.

## Alcance

Copia adquirida legítimamente, uso personal y sin distribución.
"""
    for name, payload in (
        ("00_README.md", readme),
        ("FICHA_DEL_JUEGO.md", game_sheet),
        ("CREDITOS.md", credits),
        ("PRESERVADO_POR.md", preserved),
    ):
        (docs / name).write_text(payload, encoding="utf-8", newline="\n")


def _protected_files(workspace: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    game = workspace / "neutral-object/payload/game"
    candidates: list[Path] = []
    entry = plan["layout"]["entrypoint"]
    if isinstance(entry, str) and entry:
        candidates.append(game.joinpath(*PurePosixPath(entry).parts))
    for name in ("steam_api64.dll", "steam_api.dll"):
        for candidate in game.rglob(name):
            if candidate.is_file() and not candidate.is_symlink():
                candidates.append(candidate)
    result = []
    seen: set[Path] = set()
    destination = PurePosixPath(plan["layout"]["game_destination_in_prefix"])
    for path in candidates:
        if path in seen or path.is_symlink() or not path.is_file():
            continue
        seen.add(path)
        relative = path.relative_to(game).as_posix()
        result.append(
            {
                "path": f"prefix/{destination.as_posix()}/{relative}",
                "digest": "sha256:" + sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return result


def _host_contracts(
    capsule_root: Path,
    *,
    plan: dict[str, Any],
    game_object_id: str,
    preferred_runner: dict[str, Any] | None,
    protected: list[dict[str, Any]],
) -> None:
    host = capsule_root / "host-contracts"
    host.mkdir(parents=True, exist_ok=True)
    layout = plan["layout"]
    common = {
        "schema": 0,
        "source_object": game_object_id,
        "neutral_root": "neutral-object",
        "prefix_source": "neutral-object/payload/prefix-template",
        "game_source": "neutral-object/payload/game",
        "game_destination_in_prefix": layout["game_destination_in_prefix"],
        "entrypoint_relative_to_game": layout["entrypoint"],
        "working_directory_in_prefix": layout["working_directory"],
        "baseline_state": plan["persistent_state"].get(
            "baseline_state", "embedded-or-unknown"
        ),
        "runner_binding": plan["runner"].get(
            "binding", "select-at-materialization"
        ),
        "preferred_runner": preferred_runner,
    }
    write_json(
        host / "linux-bottles.json",
        {
            **common,
            "contract": "ogv-bottles-neutral-v1",
            "flatpak_app": "com.usebottles.bottles",
            "bottle_yml_policy": "template-or-generate-derived",
            "bottle_yml_template": "evidence/source-bottles/bottle.yml",
            "network": "isolated",
        },
    )
    write_json(
        host / "linux-direct-wine.json",
        {
            **common,
            "contract": "ogv-direct-wine-neutral-v1",
            "runtime_directory": "runtime",
            "launcher": "JUGAR.sh",
            "uninstaller": "RETIRAR.sh",
            "protected_files": protected,
            "network": "host_default",
        },
    )
    write_json(
        host / "linux-umu.json",
        {
            **common,
            "contract": "ogv-umu-neutral-v1",
            "runtime_directory": "engine",
            "prefix_directory": "prefix",
            "launcher": "JUGAR.sh",
            "protected_files": protected,
            "network": "host_default",
            "notes": (
                "Perfil candidato: Proton, Steam Linux Runtime y variables "
                "STEAM_COMPAT_* deben estar declarados por el perfil final."
            ),
        },
    )
    write_json(
        host / "windows-native.json",
        {
            **common,
            "contract": "ogv-windows-export-v1",
            "include_prefix": False,
            "game_destination": "game",
            "state_destination_policy": "powershell-profile-map",
            "launcher": "JUGAR.cmd",
            "installer": "INSTALAR_ESTADO.ps1",
            "network": "host_default",
        },
    )




def _publish_bottles_evidence(
    source_root: Path,
    *,
    capsule_evidence: Path,
    private_root: Path,
) -> dict[str, Any]:
    """Preserve raw Bottles metadata privately and expose a sanitized template.

    Raw metadata can carry HOME paths and therefore belongs to the private
    workspace, not to the public capsule evidence.
    """
    result: dict[str, Any] = {
        "schema": 0,
        "raw_private_copy": None,
        "public_template": None,
        "removed_lines": 0,
    }
    if source_root.is_symlink() or not source_root.is_dir():
        return result

    raw_destination = private_root / "archived/source-bottles"
    raw_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, raw_destination, symlinks=False)
    result["raw_private_copy"] = (
        "archived/source-bottles"
    )

    template = source_root / "bottle.yml"
    if template.is_symlink() or not template.is_file():
        return result

    private_pattern = re.compile(
        r"(?i)(?:/home/|/var/home/|\\\\home\\\\|"
        r"\\\\var\\\\home\\\\|/run/user/)"
    )
    sanitized: list[str] = []
    removed = 0
    for raw in template.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if private_pattern.search(raw):
            removed += 1
            continue
        if re.match(r"^\s*Custom_Path\s*:", raw):
            indent = raw[: len(raw) - len(raw.lstrip())]
            sanitized.append(f"{indent}Custom_Path: false")
        else:
            sanitized.append(raw)
    public_root = capsule_evidence / "source-bottles"
    public_root.mkdir(parents=True, exist_ok=True)
    public_template = public_root / "bottle.yml"
    public_template.write_text(
        "\n".join(sanitized).rstrip() + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result["public_template"] = "source-bottles/bottle.yml"
    result["removed_lines"] = removed
    write_json(
        capsule_evidence / "bottles-evidence-publication.json",
        result,
    )
    return result


def _copy_supplemental(
    capsule_root: Path,
    *,
    workspace: Path,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    destination_root = capsule_root / "supplemental-content"
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(plan.get("supplemental_content", [])):
        if not isinstance(item, dict):
            raise ImporterError("supplemental_content contiene una entrada inválida")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id):
            item_id = f"content-{index:04d}"
        if item_id in seen:
            raise ImporterError(f"contenido adicional duplicado: {item_id}")
        seen.add(item_id)
        classification = item.get("classification", "supplemental-content")
        result = {
            "id": item_id,
            "classification": classification,
            "description": item.get("description", ""),
            "source_present": False,
            "status": "pending",
        }
        source = _source_for_item(workspace, item)
        if source is None:
            results.append(result)
            continue
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / item_id / source.name
        _copy_regular_or_tree(source, destination)
        result["source_present"] = True
        result["status"] = "present-and-hashed"
        result["path"] = destination.relative_to(capsule_root).as_posix()
        if destination.is_file():
            result["bytes"] = destination.stat().st_size
            result["sha256"] = sha256_file(destination)
            result["file_count"] = 1
        else:
            entries, files, directories, total = _tree_entries(destination)
            result["bytes"] = total
            result["file_count"] = files
            result["directory_count"] = directories
            result["tree_digest"] = _canonical_digest(entries)
        results.append(result)
    return results


def _document_paths(plan: dict[str, Any]) -> dict[str, str]:
    defaults = {
        "readme": "docs/00_README.md",
        "game_sheet": "docs/FICHA_DEL_JUEGO.md",
        "credits": "docs/CREDITOS.md",
        "preserved_by": "docs/PRESERVADO_POR.md",
    }
    for item in plan.get("documentation", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        canonical = item.get("canonical_name")
        if role in defaults and isinstance(canonical, str) and canonical:
            defaults[role] = f"docs/{canonical}"
    return defaults


def _publish_documentation(
    capsule_root: Path,
    *,
    workspace: Path,
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    docs = capsule_root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    selected: list[dict[str, Any]] = []
    occupied: set[str] = set()

    for item in plan.get("documentation", []):
        canonical = item["canonical_name"]
        source = _source_for_item(workspace, item)
        result: dict[str, Any] = {
            "id": item["id"],
            "role": item["role"],
            "canonical_name": canonical,
            "source_present": source is not None,
            "status": "pending",
        }
        if source is None:
            selected.append(result)
            continue
        if source.is_symlink() or not source.is_file():
            raise ImporterError(
                f"la documentación debe ser un archivo regular: {item['id']}"
            )
        destination = docs / canonical
        if canonical in occupied or destination.exists():
            raise ImporterError(
                f"documentación duplicada para {canonical}"
            )
        shutil.copy2(source, destination)
        occupied.add(canonical)
        result.update(
            {
                "status": "selected-and-hashed",
                "path": destination.relative_to(capsule_root).as_posix(),
                "bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
        selected.append(result)

    identity = plan["identity"]
    title = identity["title"]
    version = identity["preserved_version"]
    appid = identity.get("appid") or "[NO APLICA]"
    edition = identity.get("edition") or "[RELLENAR]"
    templates = {
        "00_README.md": f"""# {title} — cápsula candidata

**Edición fuente:** {edition}
**AppID:** {appid}
**Versión preservada:** {version}
**Estado:** CANDIDATO IMPORTADO

El directorio de juego fue preparado antes de la importación para funcionar sin
Steam. OfflineGameVault Importer no aplicó Steamless, no sustituyó DLLs y no
realizó cambios DRM.

## Límites

- Arranque desde el Vault: [NO PROBADO]
- Partidas: [NO PROBADO]
- DLC real: [VERIFICAR]
- Aislamiento de red: [NO PROBADO]
- Restauración limpia: [NO PROBADO]
""",
        "FICHA_DEL_JUEGO.md": f"""# FICHA DEL JUEGO — {title}

## Identificación

- Tienda fuente: {identity["source_store"]}
- AppID: {appid}
- Edición: {edition}
- Versión preservada: {version}

## Datos específicos de la copia preservada

Consultar `capsule.json`, `CONTENT_STATUS.json` y los recibos.

## Fuentes

[RELLENAR CON FUENTES PRIMARIAS]
""",
        "CREDITOS.md": f"""# CRÉDITOS — {title}

Los créditos completos permanecen en el juego.

## Roles principales

[RELLENAR DESDE CRÉDITOS IN-GAME]
""",
        "PRESERVADO_POR.md": f"""# Preservado por

Este documento firma el paquete, no la autoría del juego.

## Autoría del paquete

[RELLENAR]

## Operaciones realizadas

- Importación estructural mediante OfflineGameVault Importer {__version__}.
- Preservación de componentes seleccionados con SHA-256.
- No se aplicó Steamless ni se sustituyeron DLLs durante la importación.

## Alcance

Copia adquirida legítimamente, uso personal y sin distribución.
""",
    }
    required_roles = {
        "00_README.md": "readme",
        "FICHA_DEL_JUEGO.md": "game_sheet",
        "CREDITOS.md": "credits",
        "PRESERVADO_POR.md": "preserved_by",
    }
    selected_roles = {
        item.get("role")
        for item in plan.get("documentation", [])
        if isinstance(item, dict) and item.get("workspace_path")
    }
    for name, payload in templates.items():
        role = required_roles[name]
        target = docs / name
        if role in selected_roles:
            continue
        if not target.exists():
            target.write_text(payload, encoding="utf-8", newline="\n")
            selected.append(
                {
                    "id": f"generated-{role}",
                    "role": role,
                    "canonical_name": name,
                    "source_present": False,
                    "status": "generated-template",
                    "path": target.relative_to(capsule_root).as_posix(),
                    "bytes": target.stat().st_size,
                    "sha256": sha256_file(target),
                }
            )
    return selected

def _canonical_game_source_contract(
    legacy_contract: dict[str, Any],
) -> dict[str, Any]:
    result = dict(legacy_contract)
    result["contract"] = "ogv-game-source-v1"
    result["runner_binding"] = "select-at-materialization"
    result["preferred_runner"] = None
    result.pop("backend", None)
    result.pop("adapter", None)
    return result


def _capsule_document(
    *,
    plan: dict[str, Any],
    game_object: dict[str, Any],
    runner_object: dict[str, Any] | None,
) -> dict[str, Any]:
    identity = plan["identity"]
    game: dict[str, Any] = {
        "title": identity["title"],
        "source_store": identity["source_store"],
        "preserved_version": identity["preserved_version"],
    }
    if identity.get("edition") and identity["edition"] not in {"[RELLENAR]", ""}:
        game["edition"] = identity["edition"]
    appid = identity.get("appid")
    if isinstance(appid, str) and appid.isdigit():
        game["appid"] = int(appid)
    elif isinstance(appid, int):
        game["appid"] = appid

    objects = [game_object]
    preferred_id = None
    if runner_object is not None:
        objects.append(runner_object)
        preferred_id = runner_object["id"]

    persistent: list[dict[str, Any]] = []
    for item in plan["persistent_state"]["items"]:
        disposition = item.get("disposition")
        path = item.get("path")
        if disposition not in {"save-set", "identity", "configuration"}:
            continue
        if not isinstance(path, str) or not path:
            continue
        kind = (
            "save"
            if disposition == "save-set"
            else ("identity" if disposition == "identity" else "configuration")
        )
        source_present = bool(item.get("workspace_path") or item.get("source_path"))
        required = bool(item.get("required", False))
        if required and not source_present:
            # Candidate import must remain materializable. The downgrade is
            # recorded in CONTENT_STATUS and the import receipt.
            required = False
        declaration = {
            "id": item["id"],
            "path": path,
            "kind": kind,
            "backup": True,
            "sensitive": bool(item.get("sensitive", kind != "configuration")),
            "required": required,
            "description": item.get("description") or (
                "Imported state candidate; functional use remains untested."
            ),
        }
        persistent.append(declaration)

    game_id = game_object["id"]
    profiles: list[dict[str, Any]] = []
    destination = plan["layout"]["game_destination_in_prefix"]
    entry = plan["layout"]["entrypoint"]
    for item in plan["profiles"]:
        if not item.get("enabled"):
            continue
        adapter = item["adapter"]
        platform = item.get("platform") or (
            "windows" if adapter == "windows" else "linux"
        )
        dependencies = [game_id]
        if (
            preferred_id is not None
            and adapter in {"wine", "bottles", "umu"}
            and item.get("id") != "game-source"
        ):
            dependencies.append(preferred_id)
        if item.get("id") == "game-source":
            launch_entry = f"prefix/{destination}/{entry}"
            work = f"prefix/{plan['layout']['working_directory']}"
            contract = "host-contracts/game-source.json"
        elif adapter == "wine":
            launch_entry = f"prefix/{destination}/{entry}"
            work = f"prefix/{plan['layout']['working_directory']}"
            contract = "host-contracts/linux-direct-wine.json"
        elif adapter == "bottles":
            launch_entry = f"{destination}/{entry}"
            work = plan["layout"]["working_directory"]
            contract = "host-contracts/linux-bottles.json"
        elif adapter == "umu":
            launch_entry = f"prefix/{destination}/{entry}"
            work = f"prefix/{plan['layout']['working_directory']}"
            contract = "host-contracts/linux-umu.json"
        elif adapter == "windows":
            launch_entry = f"game/{entry}"
            work = "game"
            contract = "host-contracts/windows-native.json"
        else:
            continue
        profiles.append(
            {
                "id": item["id"],
                "platform": platform,
                "adapter": (
                    "other" if item.get("id") == "game-source" else adapter
                ),
                "status": item.get("status", "candidate"),
                "dependencies": dependencies,
                "host_contract": contract,
                "launch": {
                    "entrypoint": launch_entry,
                    "working_directory": work,
                    "arguments": [],
                    "network": (
                        "isolated" if adapter == "bottles" else "host_default"
                    ),
                },
                "notes": (
                    "Perfil candidato importado. Runner y otros parámetros "
                    "pueden seleccionarse al materializar. No hereda aceptación."
                ),
            }
        )
    if not profiles:
        raise ImporterError("no queda ningún perfil publicable")

    return {
        "schema": 0,
        "capsule_id": identity["capsule_id"],
        "sanitized_fixture": False,
        "game": game,
        "documents": _document_paths(plan),
        "objects": objects,
        "persistent_state": persistent,
        "profiles": profiles,
    }



def _runner_archive_from_directory(source: Path, destination: Path) -> tuple[str, int, str]:
    """Create a deterministic tar.gz for a manually selected runner directory.

    Relative symlinks are preserved only when their lexical target remains
    below the selected root. Special files and absolute symlinks are rejected.
    """
    source = source.resolve(strict=True)
    if source.is_symlink() or not source.is_dir():
        raise ImporterError("el runner manual no es un directorio regular")
    root_name = source.name
    if not root_name or root_name in {".", ".."}:
        raise ImporterError("el directorio runner no tiene un nombre portable")

    entries: list[tuple[Path, str, os.stat_result]] = []
    for current, directory_names, file_names in os.walk(
        source, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        retained_dirs: list[str] = []
        for name in sorted(directory_names):
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                target_path = Path(target)
                if target_path.is_absolute():
                    raise ImporterError(
                        f"runner: symlink absoluto no admitido: {relative}"
                    )
                resolved = (path.parent / target_path).resolve(strict=False)
                try:
                    resolved.relative_to(source)
                except ValueError as exc:
                    raise ImporterError(
                        f"runner: symlink escapa del directorio: {relative}"
                    ) from exc
                entries.append((path, relative, info))
            elif stat.S_ISDIR(info.st_mode):
                entries.append((path, relative, info))
                retained_dirs.append(name)
            else:
                raise ImporterError(
                    f"runner: entrada de directorio no soportada: {relative}"
                )
        directory_names[:] = retained_dirs

        for name in sorted(file_names):
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                target_path = Path(target)
                if target_path.is_absolute():
                    raise ImporterError(
                        f"runner: symlink absoluto no admitido: {relative}"
                    )
                resolved = (path.parent / target_path).resolve(strict=False)
                try:
                    resolved.relative_to(source)
                except ValueError as exc:
                    raise ImporterError(
                        f"runner: symlink escapa del directorio: {relative}"
                    ) from exc
            elif not stat.S_ISREG(info.st_mode):
                raise ImporterError(
                    f"runner: archivo especial no admitido: {relative}"
                )
            entries.append((path, relative, info))

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(
                fileobj=gz, mode="w", format=tarfile.PAX_FORMAT
            ) as archive:
                root_info = tarfile.TarInfo(root_name)
                root_info.type = tarfile.DIRTYPE
                root_info.mode = source.stat().st_mode & 0o7777
                root_info.uid = root_info.gid = 0
                root_info.uname = root_info.gname = ""
                root_info.mtime = 0
                archive.addfile(root_info)

                for path, relative, info in sorted(
                    entries, key=lambda item: item[1]
                ):
                    name = f"{root_name}/{relative}"
                    tar_info = tarfile.TarInfo(name)
                    tar_info.mode = info.st_mode & 0o7777
                    tar_info.uid = tar_info.gid = 0
                    tar_info.uname = tar_info.gname = ""
                    tar_info.mtime = 0
                    if stat.S_ISDIR(info.st_mode):
                        tar_info.type = tarfile.DIRTYPE
                        archive.addfile(tar_info)
                    elif stat.S_ISLNK(info.st_mode):
                        tar_info.type = tarfile.SYMTYPE
                        tar_info.linkname = os.readlink(path)
                        archive.addfile(tar_info)
                    else:
                        tar_info.type = tarfile.REGTYPE
                        tar_info.size = info.st_size
                        with path.open("rb") as handle:
                            archive.addfile(tar_info, handle)

    digest = sha256_file(destination)
    return digest, destination.stat().st_size, root_name


def _resolve_runner(
    workspace: Path,
    vault: Path,
    plan: dict[str, Any],
    index: dict[str, Any],
    *,
    temporary_root: Path,
) -> tuple[dict[str, Any] | None, Path | None, bool]:
    runner = plan["runner"]
    digest = runner.get("sha256")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        digest = digest.removeprefix("sha256:")
    if digest is not None and (
        not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest)
    ):
        raise ImporterError("runner.sha256 inválido")

    source: Path | None = None
    source_root_hint: str | None = None
    raw_source = runner.get("source_path")
    raw_root = runner.get("source_root")
    if isinstance(raw_source, str) and raw_source:
        candidate = Path(raw_source).expanduser()
        if not candidate.is_absolute() and isinstance(raw_root, str) and raw_root:
            candidate = Path(raw_root).expanduser() / candidate
        if not candidate.is_absolute():
            candidate = workspace / candidate
        if candidate.is_symlink() or not candidate.exists():
            raise ImporterError("runner manual ausente o symlink")
        candidate = candidate.resolve(strict=True)
        if candidate.is_dir():
            temporary_root.mkdir(parents=True, exist_ok=True)
            source = temporary_root / "manual-runner.tar.gz"
            actual, _size, source_root_hint = _runner_archive_from_directory(
                candidate, source
            )
        elif candidate.is_file():
            source = candidate
            actual = sha256_file(source)
        else:
            raise ImporterError("runner manual no es archivo ni directorio")
        if digest and actual != digest:
            raise ImporterError(
                f"hash del runner manual incorrecto: {actual} != {digest}"
            )
        digest = actual

    existing: dict[str, Any] | None = None
    if digest:
        matches = [
            item
            for item in index.get("objects", [])
            if isinstance(item, dict)
            and item.get("sha256") == digest
            and item.get("role") == "shared-runner"
        ]
        if len(matches) > 1:
            raise ImporterError("el runner aparece duplicado en INDEX.json")
        if matches:
            existing = matches[0]

    if existing is not None:
        object_id = (
            runner.get("preferred_id")
            or _slug(str(existing.get("label", "runner")).split(".tar")[0], "runner")
        )
        declaration = {
            "id": object_id,
            "digest": "sha256:" + existing["sha256"],
            "roles": ["runner"],
            "format": _archive_format(Path(existing.get("label", ""))),
            "required": True,
            "archive_path": existing["path"],
            "shared": True,
            "size": existing.get("size"),
            "description": "Runner compartido reutilizado por identidad SHA-256.",
        }
        return declaration, None, False

    if source is None:
        return None, None, False

    assert digest is not None
    object_id = runner.get("preferred_id")
    if not isinstance(object_id, str) or not _ID_RE.fullmatch(object_id):
        fallback = source_root_hint or source.name.split(".tar")[0]
        object_id = _slug(fallback, "imported-runner")
    declaration = {
        "id": object_id,
        "digest": "sha256:" + digest,
        "roles": ["runner"],
        "format": _archive_format(source),
        "required": True,
        "archive_path": _object_rel(digest),
        "shared": True,
        "size": source.stat().st_size,
        "description": "Runner compartido incorporado por OfflineGameVault Importer.",
    }
    return declaration, source, True


def _build_save_sets(
    staging_state: Path,
    *,
    workspace: Path,
    capsule: dict[str, Any],
    plan: dict[str, Any],
    operation_id: str,
    timestamp: str,
) -> dict[str, Any]:
    save_root = staging_state / "save-sets"
    save_root.mkdir(parents=True, exist_ok=True)
    declarations = {
        item["id"]: item
        for item in capsule.get("persistent_state", [])
        if item.get("kind") == "save"
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in plan["persistent_state"]["items"]:
        if item.get("disposition") != "save-set":
            continue
        groups.setdefault(item["save_set_id"], []).append(item)

    index_entries: list[dict[str, Any]] = []
    for save_set_id, selected in sorted(groups.items()):
        if not _ID_RE.fullmatch(save_set_id):
            raise ImporterError(f"save_set_id no portable: {save_set_id}")
        target = save_root / save_set_id
        payload = target / "payload"
        target.mkdir(parents=True, exist_ok=False)
        manifest_items: list[dict[str, Any]] = []
        definition_digests: dict[str, str] = {}
        for index, item in enumerate(selected):
            state_id = item["id"]
            declaration = declarations.get(state_id)
            if declaration is None:
                raise ImporterError(
                    f"{save_set_id}: {state_id} no está declarado como save"
                )
            source = _source_for_item(workspace, item)
            if source is None:
                # Unbound saves may be registered as pending metadata but are
                # not selectable until payload is supplied.
                continue
            payload_rel = PurePosixPath(
                f"payload/{index:04d}-{state_id}/data"
            )
            destination = target.joinpath(*payload_rel.parts)
            _copy_regular_or_tree(source, destination)
            entries, files, directories, total = _tree_entries(destination)
            entry_type = "file" if destination.is_file() else "directory"
            if entry_type == "file":
                digest = "sha256:" + sha256_file(destination)
            else:
                digest = _canonical_digest(entries)
            manifest_items.append(
                {
                    "state_id": state_id,
                    "kind": "save",
                    "declared_path": declaration["path"],
                    "entry_type": entry_type,
                    "payload_path": payload_rel.as_posix(),
                    "digest": digest,
                    "bytes": total,
                    "file_count": files,
                    "directory_count": directories,
                }
            )
            definition_digests[state_id] = _state_definition_digest(declaration)

        if not manifest_items:
            shutil.rmtree(target)
            continue

        aggregate = _aggregate_save_digest(manifest_items)
        display_name = selected[0].get("display_name") or save_set_id
        captured_at = selected[0].get("captured_at") or timestamp[:10]
        manifest = {
            "schema": 0,
            "contract": "ogv-save-set-v2",
            "status": "candidate",
            "capsule_id": capsule["capsule_id"],
            "save_set_id": save_set_id,
            "display_name": display_name,
            "captured_at": captured_at,
            "captured_at_basis": selected[0].get(
                "captured_at_basis", "import-user-selection"
            ),
            "items": manifest_items,
            "state_ids": [item["state_id"] for item in manifest_items],
            "save_definition_digests": definition_digests,
            "aggregate_digest": aggregate,
            "total_bytes": sum(item["bytes"] for item in manifest_items),
            "operation_id": operation_id,
            "published_at": timestamp,
            "source": {
                "type": "importer-workspace",
                "workspace_receipt": "PREPARE_RECEIPT.json",
            },
        }
        write_json(target / "save-set.json", manifest)
        manifest_digest = sha256_file(target / "save-set.json")
        entry = {
            "save_set_id": save_set_id,
            "display_name": display_name,
            "captured_at": captured_at,
            "captured_at_basis": manifest["captured_at_basis"],
            "manifest": f"{save_set_id}/save-set.json",
            "manifest_sha256": manifest_digest,
            "status": "candidate",
            "state_ids": manifest["state_ids"],
            "item_count": len(manifest_items),
            "bytes": manifest["total_bytes"],
            "aggregate_digest": aggregate,
            "source": manifest["source"],
        }
        if len(manifest_items) == 1:
            entry["state_id"] = manifest_items[0]["state_id"]
            entry["digest"] = manifest_items[0]["digest"]
        index_entries.append(entry)

    save_declarations = [
        item for item in capsule.get("persistent_state", [])
        if item.get("kind") == "save"
    ]
    index = {
        "schema": 0,
        "contract": "ogv-save-library-v2",
        "capsule_id": capsule["capsule_id"],
        "created_at": timestamp,
        "last_updated_at": timestamp,
        "last_updated_operation_id": operation_id,
        "operation_id": operation_id,
        "selection_policy": "explicit-only",
        "default_save_set_id": None,
        "status": "candidate",
        "save_set_count": len(index_entries),
        "capsule_save_definition_digest": _canonical_digest(
            [
                {
                    "id": item["id"],
                    "digest": _state_definition_digest(item),
                }
                for item in save_declarations
            ]
        ),
        "entries": index_entries,
        "new_functional_test_performed": False,
        "verification_basis": "payload-and-manifest-integrity-only",
    }
    write_json(save_root / "index.json", index)
    return index


def _build_state_root(
    root: Path,
    *,
    workspace: Path,
    capsule: dict[str, Any],
    plan: dict[str, Any],
) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    plan_items = {
        item["id"]: item for item in plan["persistent_state"]["items"]
    }
    downgrades: list[dict[str, Any]] = []
    for declaration in capsule.get("persistent_state", []):
        item = plan_items.get(declaration["id"], {})
        source = _source_for_item(workspace, item)
        if declaration["kind"] == "save":
            continue
        if source is None:
            if item.get("required"):
                downgrades.append(
                    {
                        "state_id": declaration["id"],
                        "requested_required": True,
                        "published_required": False,
                        "reason": "payload no disponible durante importación candidata",
                    }
                )
            continue
        target = root.joinpath(*PurePosixPath(declaration["path"]).parts)
        _copy_regular_or_tree(source, target)
    return downgrades


def _read_critical(vault: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for relative in _CRITICAL:
        path = vault / relative
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ImporterError(f"sello crítico no regular: {relative}")
            result[relative] = path.read_bytes()
    return result


def _critical_seal(documents: dict[str, bytes]) -> dict[str, str]:
    return {
        path: hashlib.sha256(payload).hexdigest()
        for path, payload in documents.items()
    }


@contextmanager
def _collection_lock(vault: Path, operation_id: str) -> Iterator[Path]:
    root = vault / "04_RECEIPTS/_collection"
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".ogv-import.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ImporterError(
            "otra operación de importación mantiene el bloqueo de colección"
        ) from exc
    try:
        os.write(descriptor, (operation_id + "\n").encode("utf-8"))
        os.fsync(descriptor)
        os.close(descriptor)
        yield lock
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _update_collection_sha(
    old_bytes: bytes | None,
    vault: Path,
    staged_files: dict[str, Path],
) -> bytes:
    entries: dict[str, str] = {}
    comments: list[str] = []
    if old_bytes:
        for raw in old_bytes.decode("utf-8", errors="replace").splitlines():
            stripped = raw.strip()
            match = re.match(r"^([0-9a-f]{64})\s+\*?(.+)$", stripped)
            if match:
                entries[match.group(2).removeprefix("./")] = match.group(1)
            elif raw:
                comments.append(raw)

    for relative, source in staged_files.items():
        entries[relative] = sha256_file(source)

    header = [
        "# OfflineGameVault collection manifest",
        "# SHA-256; rutas relativas a la raíz de la colección.",
    ]
    lines = [*header]
    for relative in sorted(entries):
        lines.append(f"{entries[relative]}  {relative}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _ensure_layout_dirs(capsule_id: str) -> list[str]:
    result = []
    mapping = {
        "02_CAPSULES": [
            "docs", "host-contracts", "evidence", "public-fixture",
            "supplemental-content",
        ],
        "03_PERSISTENT_STATE": [
            "accepted", "accepted/payload", "save-sets", "snapshots", "history",
        ],
        "04_RECEIPTS": [
            "acceptance", "audits", "migrations", "operations", "repairs",
        ],
        "05_PRIVATE_WORKSPACES": ["archived"],
        "06_DERIVED_MATERIALIZATIONS": ["current", "history"],
        "07_EXPORTS": ["portable", "removable-media"],
    }
    for root, children in mapping.items():
        result.append(f"{root}/{capsule_id}")
        result.extend(f"{root}/{capsule_id}/{child}" for child in children)
    return result


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.import-{uuid.uuid4().hex}")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _remove_if_empty(path: Path, stop: Path) -> None:
    current = path
    while current != stop:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def commit_workspace(
    workspace: Path,
    *,
    vault: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Publish one verified workspace as a candidate capsule.

    The operation never requires a fixed runner. Detection remains advisory;
    selected manual sources in IMPORT_PLAN.json are authoritative.
    """
    workspace = _regular_root(workspace, "workspace")
    vault = _regular_root(vault, "vault")
    raw_plan = _load_required_json(
        workspace / "IMPORT_PLAN.json",
        "IMPORT_PLAN.json",
    )
    require_prepared_plan_contract(raw_plan)
    plan = validate_plan(raw_plan, phase="commit")
    verification = verify_workspace(workspace)
    if verification.get("status") != "verified":
        raise ImporterError("el workspace no está verificado")

    privacy = _load_required_json(
        workspace / "reports/privacy-report.json",
        "privacy-report.json",
    )
    if (
        privacy.get("blocking_text_hits", 0)
        and not plan.get("policy", {}).get("allow_privacy_pending", False)
    ):
        raise ImporterError(
            "el workspace contiene hallazgos de privacidad bloqueantes"
        )

    index = _load_required_json(vault / "INDEX.json", "INDEX.json")
    layout = _load_required_json(
        vault / "COLLECTION_LAYOUT.json", "COLLECTION_LAYOUT.json"
    )
    inventory = _load_required_json(
        vault / "01_IMMUTABLE_VAULT/VAULT_INVENTORY.json",
        "VAULT_INVENTORY.json",
    )
    if index.get("schema") != 0 or layout.get("schema") != 0:
        raise ImporterError("schema de colección no admitido")

    capsule_id = plan["identity"]["capsule_id"]
    if not _ID_RE.fullmatch(capsule_id):
        raise ImporterError("capsule_id no portable")
    if (vault / "02_CAPSULES" / capsule_id).exists():
        raise ImporterError(f"la cápsula ya existe: {capsule_id}")
    if any(
        isinstance(item, dict) and item.get("capsule_id") == capsule_id
        for item in index.get("capsules", [])
    ):
        raise ImporterError(f"INDEX.json ya contiene {capsule_id}")

    receipt = _load_required_json(
        workspace / "PREPARE_RECEIPT.json", "PREPARE_RECEIPT.json"
    )
    object_info = receipt.get("neutral_object")
    if not isinstance(object_info, dict):
        raise ImporterError("PREPARE_RECEIPT no declara neutral_object")
    game_source = workspace / object_info.get("path", "objects/neutral-game.tar.gz")
    if game_source.is_symlink() or not game_source.is_file():
        raise ImporterError("falta el objeto neutral del juego")
    game_digest = sha256_file(game_source)
    if game_digest != object_info.get("sha256"):
        raise ImporterError("el objeto neutral cambió desde prepare")

    operation_id = _operation_id()
    timestamp = _now()
    before_documents = _read_critical(vault)
    before_seal = _critical_seal(before_documents)

    parent = vault / ".ogv-import-staging"
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ImporterError("el staging de importación no puede ser symlink")
    staging = parent / operation_id
    if staging.exists():
        raise ImporterError("colisión de staging")
    staging.mkdir(mode=0o700)

    try:
        runner_object, runner_source, runner_new = _resolve_runner(
            workspace,
            vault,
            plan,
            index,
            temporary_root=staging / "_runner-object",
        )
        game_object_id = "game-baseline"
        game_object = {
            "id": game_object_id,
            "digest": "sha256:" + game_digest,
            "roles": [
                "game_payload", "prefix_baseline", "configuration", "derived"
            ],
            "format": "tar.gz",
            "required": True,
            "archive_path": _object_rel(game_digest),
            "shared": False,
            "size": game_source.stat().st_size,
            "description": (
                "Objeto canónico neutral generado por OfflineGameVault Importer; "
                "contiene payload de juego y prefix-template, sin metadatos Bottles."
            ),
        }
        capsule = _capsule_document(
            plan=plan,
            game_object=game_object,
            runner_object=runner_object,
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass
        raise

    capsule_stage = staging / "02_CAPSULES" / capsule_id
    state_stage = staging / "03_PERSISTENT_STATE" / capsule_id
    receipt_stage = (
        staging / "04_RECEIPTS" / capsule_id / "operations" / operation_id
    )
    private_stage = staging / "05_PRIVATE_WORKSPACES" / capsule_id
    derived_stage = staging / "06_DERIVED_MATERIALIZATIONS" / capsule_id
    exports_stage = staging / "07_EXPORTS" / capsule_id

    published_dirs: list[Path] = []
    new_objects: list[Path] = []
    root_backups = dict(before_documents)

    try:
        documentation_inventory = _publish_documentation(
            capsule_stage,
            workspace=workspace,
            plan=plan,
        )
        protected = _protected_files(workspace, plan)
        preferred_runner = None
        if runner_object is not None:
            preferred_runner = {
                "object_id": runner_object["id"],
                "digest": runner_object["digest"],
                "binding": plan["runner"].get("binding", "preferred"),
            }
        _host_contracts(
            capsule_stage,
            plan=plan,
            game_object_id=game_object_id,
            preferred_runner=preferred_runner,
            protected=protected,
        )
        legacy_source_contract = _load_required_json(
            capsule_stage / "host-contracts/linux-direct-wine.json",
            "linux-direct-wine.json",
        )
        write_json(
            capsule_stage / "host-contracts/game-source.json",
            _canonical_game_source_contract(legacy_source_contract),
        )
        supplemental_inventory = _copy_supplemental(
            capsule_stage,
            workspace=workspace,
            plan=plan,
        )
        write_json(capsule_stage / "capsule.json", capsule)

        unresolved = plan["persistent_state"].get("unclassified_candidates", [])
        source_status = {
            "schema": 0,
            "capsule_id": capsule_id,
            "status": "candidate",
            "baseline_state": plan["persistent_state"].get(
                "baseline_state", "embedded-or-unknown"
            ),
            "unclassified_state_candidates": unresolved,
            "privacy_status": privacy.get("status"),
            "functional_retest_required": bool(
                receipt.get("functional_retest_required")
            ),
            "supplemental_content": supplemental_inventory,
            "documentation": documentation_inventory,
            "profiles": {
                item["id"]: item.get("status", "candidate")
                for item in plan["profiles"] if item.get("enabled")
            },
            "pending": [
                "functional-acceptance",
                *(
                    ["state-classification"]
                    if unresolved else []
                ),
                *(
                    ["runner-selection"]
                    if runner_object is None else []
                ),
            ],
        }
        write_json(capsule_stage / "CONTENT_STATUS.json", source_status)
        write_json(
            capsule_stage / "PROVENANCE.json",
            {
                "schema": 0,
                "capsule_id": capsule_id,
                "source_classification": "private-importer-workspace",
                "operation_id": operation_id,
                "imported_at": timestamp,
                "workspace_object_sha256": game_digest,
                "source_archive": receipt.get("source_archive"),
                "selection_authority": "IMPORT_PLAN.json",
                "automatic_detection_is_advisory": True,
            },
        )
        evidence = capsule_stage / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        for name in (
            "privacy-report.json",
            "privacy-sanitization.json",
            "state-closure.json",
            "state-extraction.json",
            "selected-components.json",
        ):
            source = workspace / "reports" / name
            if source.is_file() and not source.is_symlink():
                shutil.copy2(source, evidence / name)
        source_bottles_evidence = workspace / "evidence/source-bottles"
        bottles_evidence = _publish_bottles_evidence(
            source_bottles_evidence,
            capsule_evidence=evidence,
            private_root=private_stage,
        )
        public_plan_path = workspace / "PUBLIC_IMPORT_PLAN.json"
        if public_plan_path.is_file() and not public_plan_path.is_symlink():
            shutil.copy2(public_plan_path, evidence / "IMPORT_PLAN.json")
        else:
            write_json(evidence / "IMPORT_PLAN.json", public_plan(plan))
        shutil.copy2(
            workspace / "PREPARE_RECEIPT.json",
            evidence / "PREPARE_RECEIPT.json",
        )
        write_json(
            evidence / "protected-files.json",
            {"schema": 0, "items": protected},
        )

        for directory in (
            state_stage / "snapshots",
            state_stage / "history",
            receipt_stage,
            private_stage / "archived",
            derived_stage / "current",
            derived_stage / "history",
            exports_stage / "portable",
            exports_stage / "removable-media",
            capsule_stage / "public-fixture",
            capsule_stage / "supplemental-content",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        save_index = _build_save_sets(
            state_stage,
            workspace=workspace,
            capsule=capsule,
            plan=plan,
            operation_id=operation_id,
            timestamp=timestamp,
        )

        state_root = staging / "_state-root"
        downgrades = _build_state_root(
            state_root,
            workspace=workspace,
            capsule=capsule,
            plan=plan,
        )
        core_audit = run_core_json(
            ["audit-capsule", "--capsule", str(capsule_stage / "capsule.json")],
            plan=plan,
        )
        if not core_audit.get("valid"):
            raise ImporterError("el núcleo rechazó la cápsula candidata")

        backup_declarations = [
            item
            for item in capsule.get("persistent_state", [])
            if item.get("backup", True)
        ]
        if backup_declarations:
            preserve = run_core_json(
                [
                    "preserve-state",
                    "--capsule", str(capsule_stage / "capsule.json"),
                    "--state-root", str(state_root),
                    "--backup", str(state_stage / "accepted"),
                    "--confirm-stopped",
                ],
                plan=plan,
            )
            verify_state = run_core_json(
                [
                    "verify-state-backup",
                    "--capsule", str(capsule_stage / "capsule.json"),
                    "--backup", str(state_stage / "accepted"),
                ],
                plan=plan,
            )
            if not verify_state.get("verified"):
                raise ImporterError("el backup accepted generado no se verificó")
            state_backup_status = "verified"
        else:
            # No state was selected. The core intentionally rejects
            # preserve-state when the capsule has no backup-enabled
            # declarations, so do not manufacture an empty backup.
            preserve = {"backup_id": None}
            verify_state = {"verified": None}
            state_backup_status = "not-applicable"

        # The temporary live-state tree is never part of the published Vault.
        shutil.rmtree(state_root, ignore_errors=True)

        capsule_digest = sha256_file(capsule_stage / "capsule.json")
        game_index_entry = {
            "capsule_object_id": game_object_id,
            "label": f"{capsule_id}-neutral-game",
            "operation_id": operation_id,
            "path": game_object["archive_path"],
            "role": "game-and-prefix-candidate-baseline",
            "sha256": game_digest,
            "size": game_source.stat().st_size,
        }

        next_index = json.loads(json.dumps(index))
        next_index.setdefault("objects", []).append(game_index_entry)
        if runner_object is not None and runner_new:
            next_index["objects"].append(
                {
                    "label": Path(runner_source.name).name,
                    "path": runner_object["archive_path"],
                    "role": "shared-runner",
                    "sha256": runner_object["digest"].removeprefix("sha256:"),
                    "size": runner_object["size"],
                    "operation_id": operation_id,
                }
            )
        state_classification = (
            "clean-baseline-no-default-save"
            if source_status["baseline_state"] == "clean"
            else "source-state-embedded-or-unknown"
        )
        next_index.setdefault("capsules", []).append(
            {
                "capsule_id": capsule_id,
                "path": f"02_CAPSULES/{capsule_id}",
                "capsule": f"02_CAPSULES/{capsule_id}/capsule.json",
                "capsule_sha256": capsule_digest,
                "provenance": f"02_CAPSULES/{capsule_id}/PROVENANCE.json",
                "content_status": f"02_CAPSULES/{capsule_id}/CONTENT_STATUS.json",
                "accepted_state": f"03_PERSISTENT_STATE/{capsule_id}/accepted",
                "state_classification": state_classification,
                "save_library": {
                    "index": f"03_PERSISTENT_STATE/{capsule_id}/save-sets/index.json",
                    "save_set_count": save_index["save_set_count"],
                    "default_save_set_id": None,
                    "selection_policy": "explicit-only",
                    "status": "candidate",
                    "active_baseline_save_state": (
                        "missing"
                        if source_status["baseline_state"] == "clean"
                        else "embedded-or-unknown"
                    ),
                },
                "import_operation_id": operation_id,
            }
        )
        next_index["objects"] = sorted(
            next_index["objects"], key=lambda item: item.get("sha256", "")
        )
        next_index["capsules"] = sorted(
            next_index["capsules"], key=lambda item: item.get("capsule_id", "")
        )
        # Preserve the collection's local metadata contract while advancing
        # the operation pointer. INDEX.json is authoritative local state and
        # may carry fields not present in the public schema examples.
        if "last_updated_at" in next_index:
            next_index["last_updated_at"] = timestamp
        if "last_updated_operation_id" in next_index:
            next_index["last_updated_operation_id"] = operation_id

        next_layout = json.loads(json.dumps(layout))
        next_layout.setdefault("capsule_ids", []).append(capsule_id)
        next_layout["capsule_ids"] = sorted(set(next_layout["capsule_ids"]))
        next_layout.setdefault("directories", []).extend(
            _ensure_layout_dirs(capsule_id)
        )
        next_layout["directories"] = sorted(
            set(next_layout["directories"])
        )
        next_layout.setdefault("registrations", []).append(
            {
                "operation_id": operation_id,
                "capsule_id": capsule_id,
                "status": "candidate-imported",
                "importer_version": __version__,
                "save_set_count": save_index["save_set_count"],
                "runner_binding": plan["runner"].get("binding"),
            }
        )

        write_json(staging / "INDEX.json", next_index)
        write_json(staging / "COLLECTION_LAYOUT.json", next_layout)

        receipt_document = {
            "schema": 0,
            "operation": "import-game-via-importer-v3",
            "operation_id": operation_id,
            "status": "planned" if dry_run else "candidate-imported",
            "capsule_id": capsule_id,
            "created_at": timestamp,
            "importer_version": __version__,
            "core_version": core_version(plan),
            "source_workspace": {
                "neutral_object_sha256": game_digest,
                "prepare_receipt_sha256": sha256_file(
                    workspace / "PREPARE_RECEIPT.json"
                ),
                "import_plan_sha256": sha256_file(
                    workspace / "IMPORT_PLAN.json"
                ),
            },
            "objects": {
                "game": game_object,
                "runner": runner_object,
                "runner_new": runner_new,
            },
            "state": {
                "backup_status": state_backup_status,
                "accepted_backup_id": preserve.get("backup_id"),
                "accepted_verified": verify_state.get("verified"),
                "save_set_count": save_index["save_set_count"],
                "baseline_state": source_status["baseline_state"],
                "required_state_downgrades": downgrades,
            },
            "supplemental_content": supplemental_inventory,
            "documentation": documentation_inventory,
            "bottles_evidence": bottles_evidence,
            "profiles": [
                {
                    "id": item["id"],
                    "status": item.get("status"),
                    "adapter": item.get("adapter"),
                }
                for item in plan["profiles"] if item.get("enabled")
            ],
            "manual_selections_are_authoritative": True,
            "detection_was_advisory": True,
            "before_seal": before_seal,
            "vault_modified": not dry_run,
        }
        write_json(receipt_stage / "receipt.json", receipt_document)

        if dry_run:
            return {
                **receipt_document,
                "status": "dry-run-valid",
                "staging_removed": True,
                "vault_modified": False,
            }

        with _collection_lock(vault, operation_id):
            current_seal = _critical_seal(_read_critical(vault))
            if current_seal != before_seal:
                raise ImporterError(
                    "la colección cambió desde la validación; repita el commit"
                )

            immutable = vault / "01_IMMUTABLE_VAULT"
            game_destination = immutable / game_object["archive_path"]
            game_preexisting = game_destination.exists()
            ingest_game = run_core_json(
                [
                    "ingest-object",
                    "--source", str(game_source),
                    "--vault-root", str(immutable),
                    "--format", game_object["format"],
                    "--digest", game_object["digest"],
                    "--expected-size", str(game_object["size"]),
                ],
                plan=plan,
            )
            if not game_preexisting:
                new_objects.append(game_destination)

            ingest_runner = None
            if runner_new and runner_source is not None and runner_object is not None:
                runner_destination = immutable / runner_object["archive_path"]
                runner_preexisting = runner_destination.exists()
                ingest_runner = run_core_json(
                    [
                        "ingest-object",
                        "--source", str(runner_source),
                        "--vault-root", str(immutable),
                        "--format", runner_object["format"],
                        "--digest", runner_object["digest"],
                        "--expected-size", str(runner_object["size"]),
                    ],
                    plan=plan,
                )
                if not runner_preexisting:
                    new_objects.append(runner_destination)

            inventory_stage = staging / "VAULT_INVENTORY.json"
            run_core_json(
                [
                    "inventory",
                    "--vault-root", str(immutable),
                    "--output", str(inventory_stage),
                ],
                plan=plan,
            )
            # The official core regenerates the object facts, but this
            # collection also carries local policy/provenance metadata in
            # VAULT_INVENTORY.json. Merge rather than discard those fields.
            generated_inventory = read_json(
                inventory_stage, "inventario inmutable regenerado"
            )
            merged_inventory = json.loads(json.dumps(inventory))
            merged_inventory.update(generated_inventory)
            merged_inventory["last_updated_at"] = timestamp
            merged_inventory["last_updated_operation_id"] = operation_id
            write_json(inventory_stage, merged_inventory)

            # Seal the final receipt before it is published and before the
            # collection manifest is regenerated. Mutating it afterwards would
            # invalidate COLLECTION_SHA256.txt.
            staged_receipt_path = receipt_stage / "receipt.json"
            staged_receipt = read_json(staged_receipt_path, "receipt staged")
            staged_receipt["objects"]["game_ingest"] = ingest_game
            staged_receipt["objects"]["runner_ingest"] = ingest_runner
            write_json(staged_receipt_path, staged_receipt)

            final_roots = [
                (capsule_stage, vault / "02_CAPSULES" / capsule_id),
                (state_stage, vault / "03_PERSISTENT_STATE" / capsule_id),
                (
                    staging / "04_RECEIPTS" / capsule_id,
                    vault / "04_RECEIPTS" / capsule_id,
                ),
                (private_stage, vault / "05_PRIVATE_WORKSPACES" / capsule_id),
                (derived_stage, vault / "06_DERIVED_MATERIALIZATIONS" / capsule_id),
                (exports_stage, vault / "07_EXPORTS" / capsule_id),
            ]
            for source, destination in final_roots:
                if destination.exists() or destination.is_symlink():
                    raise ImporterError(
                        f"destino apareció durante commit: {destination}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                published_dirs.append(destination)

            staged_manifest_files: dict[str, Path] = {
                "INDEX.json": staging / "INDEX.json",
                "COLLECTION_LAYOUT.json": staging / "COLLECTION_LAYOUT.json",
                "01_IMMUTABLE_VAULT/VAULT_INVENTORY.json": inventory_stage,
            }
            for root_name in ("02_CAPSULES", "03_PERSISTENT_STATE", "04_RECEIPTS"):
                root_path = vault / root_name / capsule_id
                for path in root_path.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        staged_manifest_files[
                            path.relative_to(vault).as_posix()
                        ] = path
            collection_sha = _update_collection_sha(
                before_documents.get("COLLECTION_SHA256.txt"),
                vault,
                staged_manifest_files,
            )

            _atomic_replace_bytes(
                vault / "INDEX.json", (staging / "INDEX.json").read_bytes()
            )
            _atomic_replace_bytes(
                vault / "COLLECTION_LAYOUT.json",
                (staging / "COLLECTION_LAYOUT.json").read_bytes(),
            )
            _atomic_replace_bytes(
                immutable / "VAULT_INVENTORY.json",
                inventory_stage.read_bytes(),
            )
            _atomic_replace_bytes(
                vault / "COLLECTION_SHA256.txt", collection_sha
            )

        return {
            "schema": 0,
            "status": "candidate-imported",
            "operation_id": operation_id,
            "capsule_id": capsule_id,
            "game_object_sha256": game_digest,
            "runner": (
                runner_object["id"] if runner_object is not None else None
            ),
            "runner_binding": plan["runner"].get("binding"),
            "save_set_count": save_index["save_set_count"],
            "supplemental_content": supplemental_inventory,
            "documentation": documentation_inventory,
            "profiles": {
                item["id"]: item.get("status")
                for item in plan["profiles"] if item.get("enabled")
            },
            "receipt": (
                f"04_RECEIPTS/{capsule_id}/operations/"
                f"{operation_id}/receipt.json"
            ),
            "vault_modified": True,
        }

    except Exception:
        # Restore critical files first, then remove paths published by this
        # operation. Existing objects are never touched.
        for relative, payload in root_backups.items():
            try:
                _atomic_replace_bytes(vault / relative, payload)
            except Exception:
                pass
        for path in reversed(published_dirs):
            shutil.rmtree(path, ignore_errors=True)
        for path in new_objects:
            try:
                path.unlink()
                _remove_if_empty(
                    path.parent,
                    vault / "01_IMMUTABLE_VAULT/objects/sha256",
                )
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass
