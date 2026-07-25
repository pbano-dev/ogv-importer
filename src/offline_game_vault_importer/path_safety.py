from __future__ import annotations

from pathlib import Path, PurePosixPath
import re

from .errors import ImporterError

_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def safe_member_name(name: str) -> PurePosixPath:
    if not name or "\x00" in name:
        raise ImporterError("miembro de archivo vacío o con NUL")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _WINDOWS_ABSOLUTE.match(normalized):
        raise ImporterError(f"ruta absoluta no permitida en archivo: {name!r}")
    path = PurePosixPath(normalized)
    if any(part in ("", ".", "..") for part in path.parts):
        raise ImporterError(f"ruta no canónica o traversal en archivo: {name!r}")
    return path


def validate_link_target(
    member: PurePosixPath,
    target: str,
    *,
    allow_absolute: bool,
) -> str:
    if not target or "\x00" in target:
        raise ImporterError(f"destino de enlace inválido: {target!r}")
    normalized = target.replace("\\", "/")
    if _WINDOWS_ABSOLUTE.match(normalized):
        raise ImporterError(
            f"enlace con ruta Windows absoluta no permitido: {member} -> {target}"
        )
    if normalized.startswith("/"):
        if not allow_absolute:
            raise ImporterError(
                f"enlace absoluto no permitido: {member} -> {target}"
            )
        return normalized

    target_path = PurePosixPath(normalized)
    combined = member.parent.joinpath(target_path)
    stack: list[str] = []
    for part in combined.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise ImporterError(
                    f"enlace escapa del staging: {member} -> {target}"
                )
            stack.pop()
        else:
            stack.append(part)
    return normalized


def inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (ValueError, FileNotFoundError):
        return False
    return True
