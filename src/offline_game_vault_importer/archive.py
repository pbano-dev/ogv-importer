from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
from typing import Any, Iterable

from .errors import ImporterError
from .path_safety import validate_link_target, safe_member_name
from .util import sha256_file


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    kind: str
    size: int
    mode: int
    link_target: str | None


def _kind(member: tarfile.TarInfo) -> str:
    if member.isdir():
        return "directory"
    if member.isfile():
        return "file"
    if member.issym():
        return "symlink"
    if member.islnk():
        return "hardlink"
    return "special"


def list_tar(path: Path) -> list[ArchiveMember]:
    try:
        handle = tarfile.open(path, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ImporterError(f"no se puede abrir el archivo tar: {exc}") from exc
    members: list[ArchiveMember] = []
    with handle:
        seen: set[str] = set()
        for raw in handle:
            name = str(safe_member_name(raw.name))
            if name in seen:
                raise ImporterError(f"miembro duplicado en archivo: {name}")
            seen.add(name)
            kind = _kind(raw)
            if kind == "special":
                raise ImporterError(
                    f"tipo especial no admitido en archivo: {name}"
                )
            link_target = None
            if kind == "symlink":
                link_target = validate_link_target(PurePosixPath(name), raw.linkname, allow_absolute=True)
            if kind == "hardlink":
                # Hardlinks are rejected rather than converted silently.
                raise ImporterError(
                    f"hardlink no admitido en MVP 0.1.0: {name} -> {raw.linkname}"
                )
            members.append(
                ArchiveMember(
                    name=name,
                    kind=kind,
                    size=int(raw.size),
                    mode=int(raw.mode) & 0o7777,
                    link_target=link_target,
                )
            )
    return members


def _parents(path: PurePosixPath) -> Iterable[PurePosixPath]:
    current = path.parent
    while str(current) != ".":
        yield current
        current = current.parent


def analyze_members(members: list[ArchiveMember]) -> dict[str, Any]:
    names = {PurePosixPath(item.name): item for item in members}
    files = [path for path, item in names.items() if item.kind == "file"]
    dirs = {path for path, item in names.items() if item.kind == "directory"}
    for file_path in files:
        dirs.update(_parents(file_path))

    bottle_yml = sorted(
        str(path) for path in files if path.name.lower() == "bottle.yml"
    )

    root_scores: dict[PurePosixPath, int] = {}
    for path in files:
        lower = path.name.lower()
        if lower in ("user.reg", "system.reg", "userdef.reg"):
            root_scores[path.parent] = root_scores.get(path.parent, 0) + 2
    for directory in dirs:
        if directory.joinpath("drive_c") in dirs:
            root_scores[directory] = root_scores.get(directory, 0) + 4
    for yml in bottle_yml:
        root = PurePosixPath(yml).parent
        root_scores[root] = root_scores.get(root, 0) + 8

    bottle_roots = [
        {
            "path": str(path),
            "score": score,
            "has_drive_c": path.joinpath("drive_c") in dirs,
            "has_bottle_yml": path.joinpath("bottle.yml") in names,
        }
        for path, score in sorted(
            root_scores.items(), key=lambda pair: (-pair[1], str(pair[0]))
        )
        if score >= 4
    ]

    api_parents: set[PurePosixPath] = set()
    for path in files:
        if path.name.lower() in ("steam_api64.dll", "steam_api.dll"):
            api_parents.add(path.parent)

    game_candidates: list[dict[str, Any]] = []
    for root in sorted(api_parents, key=str):
        lower_parts = [part.lower() for part in root.parts]
        if "windows" in lower_parts and any(
            item in lower_parts for item in ("system32", "syswow64")
        ):
            continue
        subtree_files = [
            path for path in files
            if path == root or root in path.parents
        ]
        direct_files = [path for path in files if path.parent == root]
        executables = sorted(
            str(path) for path in subtree_files
            if path.suffix.lower() == ".exe"
        )
        steam_settings = any(
            path == root.joinpath("steam_settings")
            or root.joinpath("steam_settings") in path.parents
            for path in dirs | set(files)
        )
        appid = any(
            path.name.lower() == "steam_appid.txt"
            for path in direct_files
        )
        score = 10 + min(len(executables), 5)
        score += 5 if steam_settings else 0
        score += 3 if appid else 0
        game_candidates.append(
            {
                "path": str(root),
                "score": score,
                "executables": executables[:100],
                "steam_settings_present": steam_settings,
                "steam_appid_present": appid,
                "steam_api": sorted(
                    str(path) for path in direct_files
                    if path.name.lower() in ("steam_api64.dll", "steam_api.dll")
                ),
            }
        )
    game_candidates.sort(key=lambda item: (-item["score"], item["path"]))

    absolute_symlinks = [
        {"path": item.name, "target": item.link_target}
        for item in members
        if item.kind == "symlink"
        and item.link_target
        and item.link_target.startswith("/")
    ]

    return {
        "schema": 0,
        "member_count": len(members),
        "regular_file_count": sum(item.kind == "file" for item in members),
        "directory_count": sum(item.kind == "directory" for item in members),
        "symlink_count": sum(item.kind == "symlink" for item in members),
        "regular_bytes": sum(
            item.size for item in members if item.kind == "file"
        ),
        "bottle_yml_candidates": bottle_yml,
        "bottle_root_candidates": bottle_roots,
        "game_root_candidates": game_candidates,
        "absolute_symlinks": absolute_symlinks,
    }




def _state_path_candidates(
    members: list[ArchiveMember],
    hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    files = [item for item in members if item.kind == "file"]
    by_basename: dict[str, list[ArchiveMember]] = {}
    for item in files:
        basename = PurePosixPath(item.name).name.lower()
        by_basename.setdefault(basename, []).append(item)

    results: list[dict[str, Any]] = []
    for hint in hints:
        basename = str(hint.get("basename", "")).lower()
        if not basename:
            continue
        size = hint.get("size")
        matches = []
        for item in by_basename.get(basename, []):
            reasons = ["basename"]
            if isinstance(size, int) and item.size == size:
                reasons.append("size")
            elif isinstance(size, int):
                continue
            matches.append(
                {
                    "path": item.name,
                    "size": item.size,
                    "reason": "+".join(reasons),
                }
            )

        related: set[str] = set()
        for match in matches:
            path = PurePosixPath(match["path"])
            name = path.name
            base = name
            for suffix in (".bak", ".old", ".backup", ".tmp", ".prev"):
                if base.lower().endswith(suffix):
                    base = base[:-len(suffix)]
                    break
            for candidate in files:
                candidate_path = PurePosixPath(candidate.name)
                if candidate_path.parent != path.parent:
                    continue
                lower = candidate_path.name.lower()
                base_lower = base.lower()
                if (
                    lower == base_lower
                    or lower.startswith(base_lower + ".")
                    or lower.startswith(base_lower + "-")
                ):
                    related.add(candidate.name)
        results.append(
            {
                "source_archive": hint.get("source_archive"),
                "source_member": hint.get("source_member"),
                "source_size": size,
                "matches": matches,
                "related_candidates": sorted(related),
            }
        )
    return results


def inspect_tar(
    path: Path,
    *,
    full_hash: bool = False,
    match_hints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    members = list_tar(path)
    report = analyze_members(members)
    report["archive_name"] = path.name
    report["archive_bytes"] = path.stat().st_size
    report["archive_sha256"] = sha256_file(path) if full_hash else None
    report["full_hash_verified"] = full_hash
    report["state_path_candidates"] = _state_path_candidates(
        members, match_hints or []
    )
    return report


def safe_extract_tar(path: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise ImporterError(f"el destino de extracción ya existe: {destination}")
    destination.mkdir(parents=True, mode=0o700)

    try:
        handle = tarfile.open(path, mode="r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ImporterError(f"no se puede abrir el archivo tar: {exc}") from exc

    extracted = 0
    symlink_members: set[PurePosixPath] = set()
    with handle:
        seen: set[str] = set()
        for raw in handle:
            member = safe_member_name(raw.name)
            name = str(member)
            if name in seen:
                raise ImporterError(f"miembro duplicado: {name}")
            seen.add(name)
            if any(parent in symlink_members for parent in member.parents):
                raise ImporterError(
                    f"miembro situado bajo un symlink del archivo: {name}"
                )
            if raw.issym():
                for prior in seen:
                    prior_path = PurePosixPath(prior)
                    if member in prior_path.parents:
                        raise ImporterError(
                            f"symlink declarado después de descendientes: {name}"
                        )
            target = destination.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)

            if raw.isdir():
                target.mkdir(exist_ok=True)
                os.chmod(target, raw.mode & 0o777)
            elif raw.isfile():
                if target.exists() or target.is_symlink():
                    raise ImporterError(f"colisión durante extracción: {name}")
                source = handle.extractfile(raw)
                if source is None:
                    raise ImporterError(f"no se pudo leer el miembro: {name}")
                temporary = target.with_name(target.name + ".part")
                with source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
                os.chmod(temporary, raw.mode & 0o777)
                os.replace(temporary, target)
            elif raw.issym():
                link_target = validate_link_target(member, raw.linkname, allow_absolute=True)
                if target.exists() or target.is_symlink():
                    raise ImporterError(f"colisión de symlink: {name}")
                target.symlink_to(link_target)
                symlink_members.add(member)
            elif raw.islnk():
                raise ImporterError(
                    f"hardlink no admitido en MVP 0.1.0: {name}"
                )
            else:
                raise ImporterError(f"tipo especial no admitido: {name}")
            extracted += 1

    return {"schema": 0, "member_count": extracted, "status": "extracted"}
