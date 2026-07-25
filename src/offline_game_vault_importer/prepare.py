from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import uuid
from typing import Any, Callable

from .archive import safe_extract_tar
from .errors import ImporterError
from .planner import validate_plan
from .sanitization import apply_sanitization_rules
from .util import canonical_json_bytes, sha256_file, write_json


def _resolve_under(root: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ImporterError(f"{label} no es una ruta relativa segura: {relative}")
    candidate = root.joinpath(*pure.parts)
    # Do not resolve through possible absolute Wine symlinks.
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink() and current != candidate:
            raise ImporterError(
                f"{label} atraviesa un symlink: {relative}"
            )
    if candidate.is_symlink():
        raise ImporterError(f"{label} no puede ser un symlink: {relative}")
    if not candidate.exists():
        raise ImporterError(f"{label} no existe: {relative}")
    return candidate


def _relative_set(paths: list[str]) -> set[PurePosixPath]:
    result: set[PurePosixPath] = set()
    for raw in paths:
        pure = PurePosixPath(raw)
        if pure.is_absolute() or ".." in pure.parts:
            raise ImporterError(f"ruta de exclusión insegura: {raw}")
        result.add(pure)
    return result


def _excluded(path: PurePosixPath, excludes: set[PurePosixPath]) -> bool:
    return path in excludes or any(parent in excludes for parent in path.parents)


def _copy_tree_filtered(
    source: Path,
    destination: Path,
    *,
    excludes: set[PurePosixPath],
) -> None:
    if destination.exists():
        raise ImporterError(f"destino ya existente: {destination}")
    destination.mkdir(parents=True, mode=stat.S_IMODE(source.lstat().st_mode))

    def visit(src: Path, dst: Path, relative: PurePosixPath) -> None:
        entries = sorted(os.scandir(src), key=lambda item: item.name)
        for entry in entries:
            rel = relative / entry.name
            if _excluded(rel, excludes):
                continue
            src_path = Path(entry.path)
            dst_path = dst / entry.name
            info = src_path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                dst_path.symlink_to(os.readlink(src_path))
            elif stat.S_ISDIR(info.st_mode):
                dst_path.mkdir(mode=mode)
                visit(src_path, dst_path, rel)
                os.chmod(dst_path, mode)
                os.utime(
                    dst_path,
                    ns=(info.st_atime_ns, info.st_mtime_ns),
                    follow_symlinks=False,
                )
            elif stat.S_ISREG(info.st_mode):
                shutil.copy2(src_path, dst_path, follow_symlinks=False)
            else:
                raise ImporterError(
                    f"archivo especial no admitido al copiar: {src_path.name}"
                )

    visit(source, destination, PurePosixPath())
    root_info = source.lstat()
    os.chmod(destination, stat.S_IMODE(root_info.st_mode))
    os.utime(
        destination,
        ns=(root_info.st_atime_ns, root_info.st_mtime_ns),
        follow_symlinks=False,
    )


def _copy_item(source: Path, destination: Path) -> None:
    info = source.lstat()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if stat.S_ISLNK(info.st_mode):
        destination.symlink_to(os.readlink(source))
    elif stat.S_ISDIR(info.st_mode):
        _copy_tree_filtered(source, destination, excludes=set())
    elif stat.S_ISREG(info.st_mode):
        shutil.copy2(source, destination, follow_symlinks=False)
    else:
        raise ImporterError(f"estado con tipo especial no admitido: {source}")


def _related_candidates(path: Path) -> list[Path]:
    if not path.exists() or not path.is_file():
        return []
    name = path.name
    base = name
    for suffix in (".bak", ".old", ".backup", ".tmp", ".prev"):
        if base.lower().endswith(suffix):
            base = base[:-len(suffix)]
            break
    result = []
    for sibling in path.parent.iterdir():
        if sibling.is_symlink() or not sibling.is_file():
            continue
        lower = sibling.name.lower()
        base_lower = base.lower()
        if (
            lower == base_lower
            or lower.startswith(base_lower + ".")
            or lower.startswith(base_lower + "-")
        ):
            result.append(sibling)
    return sorted(result, key=lambda item: item.name.lower())


def _verify_related_closure(
    bottle_root: Path,
    state_items: list[dict[str, Any]],
    exceptions: list[str],
    *,
    require_closed: bool,
) -> dict[str, Any]:
    declared = {
        PurePosixPath(item["path"])
        for item in state_items
        if isinstance(item.get("path"), str)
        and item.get("path")
        and item.get("disposition") not in {"unbound", "embedded"}
    }
    ignored = {PurePosixPath(item) for item in exceptions}
    candidates: set[PurePosixPath] = set()
    missing_declared: list[str] = []
    for item in state_items:
        raw = item.get("path")
        if (
            not isinstance(raw, str)
            or not raw
            or item.get("disposition") in {"unbound", "embedded"}
        ):
            continue
        pure = PurePosixPath(raw)
        candidate = bottle_root.joinpath(*pure.parts)
        if candidate.is_symlink():
            raise ImporterError(f"estado {item['id']} es un symlink")
        if not candidate.exists():
            # An explicitly selected external source may legitimately not be
            # embedded in the Bottle.
            if item.get("source_path"):
                continue
            missing_declared.append(raw)
            continue
        for related in _related_candidates(candidate):
            candidates.add(
                PurePosixPath(related.relative_to(bottle_root).as_posix())
            )
    unresolved = sorted(str(path) for path in candidates - declared - ignored)
    if require_closed and unresolved:
        raise ImporterError(
            "cierre de estado incompleto; clasifique estos compañeros: "
            + ", ".join(unresolved)
        )
    return {
        "schema": 0,
        "declared": sorted(str(path) for path in declared),
        "related_candidates": sorted(str(path) for path in candidates),
        "documented_exceptions": sorted(str(path) for path in ignored),
        "unresolved": unresolved,
        "missing_declared": sorted(missing_declared),
        "status": "closed" if not unresolved else "partial",
    }


def _walk_no_follow(root: Path):
    def visit(directory: Path):
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            yield path
            if entry.is_dir(follow_symlinks=False):
                yield from visit(path)
    yield from visit(root)


def _inventory(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in _walk_no_follow(root):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            entries.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "mode": mode,
                    "target": os.readlink(path),
                }
            )
        elif stat.S_ISDIR(info.st_mode):
            entries.append(
                {"path": relative, "type": "directory", "mode": mode}
            )
        elif stat.S_ISREG(info.st_mode):
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": mode,
                    "size": info.st_size,
                    "sha256": sha256_file(path),
                }
            )
        else:
            raise ImporterError(f"tipo especial en objeto neutral: {relative}")
    return {
        "schema": 0,
        "algorithm": "SHA-256",
        "entry_count": len(entries),
        "entries": entries,
    }


def _tarinfo(path: Path, arcname: str) -> tarfile.TarInfo:
    info = path.lstat()
    item = tarfile.TarInfo(arcname)
    item.uid = 0
    item.gid = 0
    item.uname = ""
    item.gname = ""
    item.mode = stat.S_IMODE(info.st_mode)
    item.mtime = int(info.st_mtime)
    if stat.S_ISDIR(info.st_mode):
        item.type = tarfile.DIRTYPE
        item.size = 0
    elif stat.S_ISLNK(info.st_mode):
        item.type = tarfile.SYMTYPE
        item.linkname = os.readlink(path)
        item.size = 0
    elif stat.S_ISREG(info.st_mode):
        item.type = tarfile.REGTYPE
        item.size = info.st_size
    else:
        raise ImporterError(f"tipo especial al empaquetar: {arcname}")
    return item


def _build_deterministic_tar_gz(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            mtime=0,
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                paths = [source, *_walk_no_follow(source)]
                for path in paths:
                    arcname = (
                        source.name
                        if path == source
                        else f"{source.name}/{path.relative_to(source).as_posix()}"
                    )
                    item = _tarinfo(path, arcname)
                    if item.isreg():
                        with path.open("rb") as handle:
                            archive.addfile(item, handle)
                    else:
                        archive.addfile(item)
    os.replace(temporary, destination)


def _privacy_report(
    neutral_root: Path,
    *,
    source_root: Path | None,
    package_name: str,
) -> dict[str, Any]:
    text_hits: list[dict[str, Any]] = []
    symlinks: list[dict[str, Any]] = []
    source_token = str(source_root) if source_root is not None else ""
    patterns = [
        ("/home/", "/home/"),
        ("\\\\home\\\\", "\\\\home\\\\"),
        ("/run/user/", "/run/user/"),
        ("\\\\run\\\\user\\\\", "\\\\run\\\\user\\\\"),
        (source_token, "<SOURCE_ROOT>"),
        (package_name, "<SOURCE_PACKAGE_NAME>"),
    ]
    text_suffixes = {
        ".txt", ".md", ".json", ".ini", ".cfg", ".conf", ".reg",
        ".yml", ".yaml", ".xml", ".log", ".acf",
    }
    blocking_occurrences = 0
    blocking_files: set[str] = set()
    for path in _walk_no_follow(neutral_root):
        relative = path.relative_to(neutral_root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            category = "absolute" if target.startswith("/") else "relative"
            symlinks.append(
                {"path": relative, "target": target, "category": category}
            )
            continue
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            continue
        if path.suffix.lower() not in text_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for pattern, label in patterns:
            if not pattern:
                continue
            occurrences = text.count(pattern)
            if occurrences:
                text_hits.append(
                    {
                        "path": relative,
                        "pattern": label,
                        "occurrences": occurrences,
                    }
                )
                blocking_occurrences += occurrences
                blocking_files.add(relative)
    return {
        "schema": 0,
        "text_hits": text_hits,
        "symlinks": symlinks,
        "blocking_text_hits": blocking_occurrences,
        "blocking_files": len(blocking_files),
        "status": "review-required" if blocking_occurrences else "no-text-hits",
        "note": (
            "blocking_text_hits cuenta coincidencias, no ficheros. "
            "Los symlinks absolutos se informan por separado. "
            "dosdevices/z: -> / puede ser deliberado y no debe sanearse a ciegas."
        ),
    }


def _prepare_workspace_in_place(
    plan: dict[str, Any],
    *,
    source_package: Path,
    workspace: Path,
) -> dict[str, Any]:
    plan = validate_plan(plan, phase="prepare")
    if workspace.exists():
        raise ImporterError(f"el workspace ya existe: {workspace}")
    workspace.mkdir(parents=True, mode=0o700)

    package = source_package.resolve(strict=True)
    if package.is_symlink() or not package.is_dir():
        raise ImporterError("source-package no es un directorio regular")
    write_json(workspace / "IMPORT_PLAN.json", plan)
    archive_rel = plan["source"]["game_archive"]
    archive_path = _resolve_under(package, archive_rel, "archivo jugable")
    expected = plan["source"]["game_archive_sha256"]
    actual = sha256_file(archive_path)
    if actual != expected:
        raise ImporterError(
            f"el archivo fuente cambió: esperado {expected}, obtenido {actual}"
        )

    extracted = workspace / "source-extracted"
    extraction = safe_extract_tar(archive_path, extracted)
    bottle_root = _resolve_under(
        extracted,
        plan["source"]["bottle_root_in_archive"],
        "raíz de bottle",
    )
    if not bottle_root.is_dir() or bottle_root.is_symlink():
        raise ImporterError("la raíz de bottle no es un directorio regular")

    game_root = _resolve_under(
        extracted,
        plan["layout"]["game_root_in_archive"],
        "raíz de juego",
    )
    if not game_root.is_dir() or game_root.is_symlink():
        raise ImporterError("la raíz de juego no es un directorio regular")

    try:
        game_relative = game_root.relative_to(bottle_root)
    except ValueError as exc:
        raise ImporterError("el juego no está dentro de la raíz de bottle") from exc
    expected_destination = PurePosixPath(
        plan["layout"]["game_destination_in_prefix"]
    )
    if game_relative.as_posix() != expected_destination.as_posix():
        raise ImporterError(
            "game_destination_in_prefix no coincide con el archivo fuente"
        )

    state = plan["persistent_state"]
    state_items = state["items"]
    closure = _verify_related_closure(
        bottle_root,
        state_items,
        state.get("related_file_exceptions", []),
        require_closed=bool(state.get("require_related_file_closure", False)),
    )

    neutral_root = workspace / "neutral-object"
    payload = neutral_root / "payload"
    game_destination = payload / "game"
    prefix_destination = payload / "prefix-template"
    state_destination = workspace / "private-state"

    state_paths = [
        item["path"]
        for item in state_items
        if isinstance(item.get("path"), str)
        and item.get("path")
        and item.get("disposition") not in {"embedded", "unbound"}
    ]
    metadata_paths = plan["layout"].get("bottles_metadata_paths", [])
    prefix_excludes = _relative_set(
        [game_relative.as_posix(), *state_paths, *metadata_paths]
    )

    game_excludes: list[str] = []
    for raw in state_paths:
        pure = PurePosixPath(raw)
        try:
            game_excludes.append(str(pure.relative_to(game_relative)))
        except ValueError:
            pass

    _copy_tree_filtered(
        game_root,
        game_destination,
        excludes=_relative_set(game_excludes),
    )
    _copy_tree_filtered(
        bottle_root,
        prefix_destination,
        excludes=prefix_excludes,
    )

    state_receipt_items: list[dict[str, Any]] = []
    for item in state_items:
        disposition = item["disposition"]
        raw_path = item.get("path")
        source: Path | None = None
        source_resolution = "missing"
        external = item.get("source_path")
        archive_candidate: Path | None = None
        if (
            isinstance(raw_path, str)
            and raw_path
            and disposition not in {"unbound", "embedded"}
        ):
            archive_candidate = bottle_root.joinpath(*PurePosixPath(raw_path).parts)

        if isinstance(external, str) and external:
            manual_candidate = Path(external).expanduser()
            if not manual_candidate.is_absolute():
                manual_candidate = package / manual_candidate
            if manual_candidate.exists():
                if manual_candidate.is_symlink():
                    raise ImporterError(
                        f"fuente manual de estado es symlink: {item['id']}"
                    )
                source = manual_candidate.resolve(strict=True)
                source_resolution = "manual"
            elif (
                archive_candidate is not None
                and archive_candidate.exists()
                and not archive_candidate.is_symlink()
            ):
                # GUI 0.2.1 could persist a stale or package-relative
                # source_path even when the authoritative state item lived
                # inside the Full Archive. In that case, the declared
                # destination path remains sufficient to resolve it safely.
                source = archive_candidate
                source_resolution = "archive-path-fallback"
            else:
                raise ImporterError(
                    f"fuente de estado no encontrada: {item['id']}"
                )
        elif (
            archive_candidate is not None
            and archive_candidate.exists()
            and not archive_candidate.is_symlink()
        ):
            source = archive_candidate
            source_resolution = "archive-path"

        receipt = {
            "id": item["id"],
            "path": raw_path,
            "disposition": disposition,
            "present_in_source": source is not None,
            "source_resolution": source_resolution,
        }
        if source is not None and disposition not in {"exclude", "embedded", "unbound"}:
            if disposition == "save-set":
                group = item["save_set_id"]
            else:
                group = disposition
            destination = state_destination / group / item["id"] / source.name
            _copy_item(source, destination)
            if destination.is_file() and not destination.is_symlink():
                receipt["sha256"] = sha256_file(destination)
                receipt["size"] = destination.stat().st_size
            receipt["workspace_path"] = destination.relative_to(workspace).as_posix()
            item["workspace_path"] = receipt["workspace_path"]
        state_receipt_items.append(receipt)

    # Persist the authoritative, normalized plan after workspace payload paths
    # have been assigned. The commit phase must not depend on transient Python
    # mutations that were never sealed into IMPORT_PLAN.json.
    write_json(workspace / "IMPORT_PLAN.json", plan)

    evidence = workspace / "evidence/source-bottles"
    for raw in metadata_paths:
        source = _resolve_under(bottle_root, raw, "metadato Bottles")
        _copy_item(source, evidence / PurePosixPath(raw))

    sanitization = apply_sanitization_rules(neutral_root, plan)
    write_json(
        workspace / "reports/privacy-sanitization.json",
        sanitization,
    )

    layout_contract = {
        "schema": 0,
        "contract": "ogv-neutral-game-layout-v1",
        "payload": {
            "game": "payload/game",
            "prefix_template": "payload/prefix-template",
        },
        "materialization": {
            "game_destination_in_prefix": game_relative.as_posix(),
            "entrypoint_relative_to_game": plan["layout"]["entrypoint"],
            "working_directory_in_prefix": plan["layout"]["working_directory"],
        },
        "source": {
            "format": "bottles-full-archive",
            "bottles_metadata_embedded": False,
        },
        "profiles": plan["profiles"],
        "core_compatibility": {
            "offline_game_vault_0_9_0_commit_supported": True,
            "reason": (
                "El commit publica perfiles candidate; la GUI 0.2.1 genera "
                "adaptadores derivados al seleccionar runner y backend."
            ),
        },
    }
    write_json(neutral_root / "NEUTRAL_LAYOUT.json", layout_contract)

    inventory = _inventory(neutral_root)
    write_json(neutral_root / "INVENTORY.json", inventory)
    # Inventory excludes itself by construction. Record this explicitly.
    inventory_note = {
        "schema": 0,
        "inventory_scope": "neutral-object before INVENTORY.json",
        "inventory_sha256": sha256_file(neutral_root / "INVENTORY.json"),
    }
    write_json(neutral_root / "INVENTORY_SEAL.json", inventory_note)

    privacy = _privacy_report(
        neutral_root,
        source_root=package,
        package_name=package.name,
    )
    write_json(workspace / "reports/privacy-report.json", privacy)
    write_json(workspace / "reports/state-closure.json", closure)
    write_json(
        workspace / "reports/state-extraction.json",
        {"schema": 0, "items": state_receipt_items},
    )

    object_archive = workspace / "objects/neutral-game.tar.gz"
    _build_deterministic_tar_gz(neutral_root, object_archive)
    object_digest = sha256_file(object_archive)

    blockers = ["Completar aceptación funcional."]
    if closure.get("status") != "closed":
        blockers.append(
            "El baseline puede contener estado no clasificado; no se garantiza "
            "la opción Sin partida."
        )
    if privacy["blocking_text_hits"]:
        blockers.append("Resolver hallazgos de privacidad, si existen.")
    if sanitization.get("functional_retest_required"):
        blockers.append(
            "Revalidar representación de texto: se retiraron referencias "
            "a fuentes externas ausentes del prefix."
        )

    input_draft = {
        "schema": 0,
        "contract": "ogv-capsule-input-draft-v1",
        "status": "candidate",
        "identity": plan["identity"],
        "neutral_object": {
            "path": object_archive.relative_to(workspace).as_posix(),
            "sha256": object_digest,
            "bytes": object_archive.stat().st_size,
            "roles": ["game_payload", "prefix_baseline"],
        },
        "runner": plan["runner"],
        "persistent_state": state_items,
        "profiles": plan["profiles"],
        "not_ready_for_vault_commit": False,
        "blockers": blockers,
    }
    write_json(workspace / "draft/CAPSULE_INPUT.json", input_draft)

    receipt = {
        "schema": 0,
        "operation": "prepare-neutral-import-workspace",
        "tool_version": "0.2.1",
        "status": (
            "candidate-needs-privacy-review"
            if privacy["blocking_text_hits"]
            else "candidate-prepared"
        ),
        "source_archive": {
            "name": Path(archive_rel).name,
            "sha256": actual,
            "bytes": archive_path.stat().st_size,
        },
        "extraction": extraction,
        "neutral_object": {
            "path": object_archive.relative_to(workspace).as_posix(),
            "sha256": object_digest,
            "bytes": object_archive.stat().st_size,
        },
        "state_items": state_receipt_items,
        "privacy_sanitization": sanitization,
        "functional_retest_required": sanitization.get("functional_retest_required", False),
        "vault_modified": False,
    }
    write_json(workspace / "PREPARE_RECEIPT.json", receipt)

    shutil.rmtree(extracted)
    return receipt


def prepare_workspace(
    plan: dict[str, Any],
    *,
    source_package: Path,
    workspace: Path,
) -> dict[str, Any]:
    final = workspace.expanduser()
    if final.is_symlink():
        raise ImporterError(f"el workspace es un symlink: {final}")
    if final.exists():
        if not final.is_dir():
            raise ImporterError(f"el workspace ya existe: {final}")
        existing = sorted(
            child.name for child in final.iterdir()
        )
        if existing == ["IMPORT_PLAN.json"] and (
            final / "IMPORT_PLAN.json"
        ).is_file():
            # GUI 0.2.0 created a plan-only directory when validating before
            # preparation. It is safe to replace only this exact, known shape.
            shutil.rmtree(final)
        else:
            raise ImporterError(f"el workspace ya existe: {final}")
    parent = final.parent
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise ImporterError("el padre del workspace no es un directorio regular")
    staging = parent / f".{final.name}.tmp-{uuid.uuid4().hex}"
    try:
        receipt = _prepare_workspace_in_place(
            plan,
            source_package=source_package,
            workspace=staging,
        )
        os.replace(staging, final)
        return receipt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
