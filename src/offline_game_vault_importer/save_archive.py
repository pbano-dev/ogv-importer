from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import stat
from typing import Any
import zipfile

from .errors import ImporterError
from .path_safety import safe_member_name
from .util import sha256_file


def inspect_save_zip(path: Path, *, full_hash: bool = False) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ImporterError(f"ZIP de partidas inválido: {path.name}: {exc}") from exc

    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    with archive:
        for info in archive.infolist():
            member = safe_member_name(info.filename.rstrip("/"))
            name = member.as_posix()
            if name in seen:
                raise ImporterError(
                    f"miembro duplicado en ZIP de partidas: {name}"
                )
            seen.add(name)
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise ImporterError(
                    f"symlink no admitido en ZIP de partidas: {name}"
                )
            if info.is_dir():
                kind = "directory"
                digest = None
            else:
                kind = "file"
                digest = None
                if full_hash:
                    hasher = hashlib.sha256()
                    with archive.open(info, "r") as handle:
                        while chunk := handle.read(1024 * 1024):
                            hasher.update(chunk)
                    digest = hasher.hexdigest()
            members.append(
                {
                    "path": name,
                    "basename": member.name,
                    "kind": kind,
                    "size": int(info.file_size),
                    "sha256": digest,
                }
            )
    return {
        "schema": 0,
        "archive_name": path.name,
        "archive_bytes": path.stat().st_size,
        "archive_sha256": sha256_file(path) if full_hash else None,
        "full_hash_verified": full_hash,
        "member_count": len(members),
        "members": members,
    }
