from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

from .errors import ImporterError


_SECTION_RE = re.compile(r"^\[(.*?)\](?:\s+\d+)?$")
_VALUE_RE = re.compile(r'^"([^"]+)"="(.*)"$')
_REG_VALUE_RE = re.compile(r'^"([^"]+)"=(?:(str\(\d+\):))?"(.*)"$')

_FONT_SECTIONS = {
    r"software\microsoft\windows\currentversion\fonts",
    r"software\microsoft\windows nt\currentversion\fonts",
    r"software\wine\fonts\external fonts",
}


def _safe_relative_file(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise ImporterError(f"ruta de saneamiento insegura: {relative}")
    target = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ImporterError(
                f"la ruta de saneamiento atraviesa un symlink: {relative}"
            )
    if not target.is_file():
        raise ImporterError(
            f"fichero de saneamiento ausente o no regular: {relative}"
        )
    return target


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _registry_path_basename(value: str) -> str:
    normalized = value.replace("\\\\", "/").replace("\\", "/")
    return PurePosixPath(normalized).name


def _registry_normalize(value: str) -> str:
    normalized = value
    while "\\\\" in normalized:
        normalized = normalized.replace("\\\\", "\\")
    return normalized.lower()


def _contains_private_host_path(value: str) -> bool:
    normalized = _registry_normalize(value)
    return any(
        token in normalized
        for token in (
            "/home/",
            "\\home\\",
            "/var/home/",
            "\\var\\home\\",
            "/run/user/",
            "\\run\\user\\",
        )
    )


def _normalized_section(section: str | None) -> str:
    if section is None:
        return ""
    return _registry_normalize(section)


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _render_value(name: str, prefix: str | None, value: str, ending: str) -> str:
    value_prefix = prefix or ""
    return f'"{name}"={value_prefix}"{value}"{ending}'


def _font_map(neutral_root: Path) -> dict[str, str]:
    font_root = (
        neutral_root
        / "payload"
        / "prefix-template"
        / "drive_c"
        / "windows"
        / "Fonts"
    )
    result: dict[str, str] = {}
    if font_root.is_dir() and not font_root.is_symlink():
        for path in font_root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                result.setdefault(path.name.casefold(), path.name)
    return result


def _automatic_registry_sanitize_file(
    neutral_root: Path,
    path: Path,
    *,
    internal_fonts: dict[str, str],
) -> dict[str, Any]:
    before = path.read_bytes()
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "file": path.relative_to(neutral_root).as_posix(),
            "status": "skipped-non-utf8",
            "changes": [],
            "sha256_before": _sha256_bytes(before),
            "sha256_after": _sha256_bytes(before),
        }

    current: str | None = None
    output: list[str] = []
    changes: list[dict[str, Any]] = []

    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        stripped = line.rstrip("\r\n")
        section_match = _SECTION_RE.match(stripped.strip())
        if section_match:
            current = section_match.group(1)
            output.append(line)
            continue

        value_match = _REG_VALUE_RE.match(stripped.strip())
        if not value_match:
            output.append(line)
            continue

        name, value_prefix, value = value_match.groups()
        if not _contains_private_host_path(value):
            output.append(line)
            continue

        section = _normalized_section(current)
        lower_name = name.casefold()
        ending = _line_ending(line)

        # Windows Installer provenance: it is host-specific and not required
        # for executing an already installed component.
        if section.endswith(r"\sourcelist") and lower_name == "lastusedsource":
            changes.append(
                {
                    "line": number,
                    "section": current,
                    "value": name,
                    "action": "removed-installer-source",
                }
            )
            continue

        if r"\sourcelist\net" in section:
            changes.append(
                {
                    "line": number,
                    "section": current,
                    "value": name,
                    "action": "removed-installer-network-source",
                }
            )
            continue

        if section.endswith(r"\sourcelist") and lower_name == "packagename":
            basename = _registry_path_basename(value)
            if not basename or "/" in basename or "\\" in basename:
                raise ImporterError(
                    f"PackageName no saneable en {path.name}:{number}"
                )
            output.append(
                _render_value(name, value_prefix, basename, ending)
            )
            changes.append(
                {
                    "line": number,
                    "section": current,
                    "value": name,
                    "action": "reduced-package-name-to-basename",
                    "basename": basename,
                }
            )
            continue

        if lower_name == "installsource" and (
            r"\installproperties" in section
            or r"\uninstall\\" in section
            or r"\uninstall\{" in section
        ):
            changes.append(
                {
                    "line": number,
                    "section": current,
                    "value": name,
                    "action": "removed-installer-install-source",
                }
            )
            continue

        # External font registry entries generated from host fonts are not
        # portable. Repoint only when an actual copy is already inside the
        # prefix; otherwise remove the dangling registration and require a
        # text-rendering acceptance test.
        if section in _FONT_SECTIONS:
            basename = PureWindowsPath(
                _registry_normalize(value).replace("/", "\\")
            ).name
            internal = internal_fonts.get(basename.casefold())
            if internal:
                portable = rf"C:\\windows\\Fonts\\{internal}"
                output.append(
                    _render_value(name, value_prefix, portable, ending)
                )
                changes.append(
                    {
                        "line": number,
                        "section": current,
                        "value": name,
                        "action": "repointed-font-to-prefix",
                        "basename": internal,
                    }
                )
            else:
                changes.append(
                    {
                        "line": number,
                        "section": current,
                        "value": name,
                        "action": "removed-missing-external-font-reference",
                        "basename": basename,
                        "functional_retest_required": True,
                    }
                )
            continue

        # Unknown references remain untouched and therefore continue to block
        # privacy acceptance.
        output.append(line)

    after = "".join(output).encode("utf-8")
    if after != before:
        temporary = path.with_name(path.name + ".auto-sanitize-part")
        temporary.write_bytes(after)
        os.replace(temporary, path)

    return {
        "file": path.relative_to(neutral_root).as_posix(),
        "status": "sanitized" if after != before else "unchanged",
        "changes": changes,
        "sha256_before": _sha256_bytes(before),
        "sha256_after": _sha256_bytes(after),
    }


def auto_sanitize_known_host_paths(neutral_root: Path) -> dict[str, Any]:
    internal_fonts = _font_map(neutral_root)
    prefix = neutral_root / "payload" / "prefix-template"
    results: list[dict[str, Any]] = []
    for name in ("system.reg", "user.reg", "userdef.reg"):
        path = prefix / name
        if path.is_file() and not path.is_symlink():
            result = _automatic_registry_sanitize_file(
                neutral_root,
                path,
                internal_fonts=internal_fonts,
            )
            if result["changes"] or result["status"] == "skipped-non-utf8":
                results.append(result)

    changes = [
        change
        for result in results
        for change in result.get("changes", [])
    ]
    actions: dict[str, int] = {}
    for change in changes:
        action = change["action"]
        actions[action] = actions.get(action, 0) + 1

    return {
        "schema": 0,
        "mode": "automatic-known-host-paths-v1",
        "files_changed": sum(
            1 for result in results if result["status"] == "sanitized"
        ),
        "changes_applied": len(changes),
        "actions": dict(sorted(actions.items())),
        "functional_retest_required": any(
            change.get("functional_retest_required") is True
            for change in changes
        ),
        "results": results,
        "status": "sanitized" if changes else "no-known-host-paths",
    }


def _sanitize_source_list_rule(
    neutral_root: Path,
    rule: dict[str, Any],
) -> dict[str, Any]:
    rule_id = rule["id"]
    relative = rule["file"]
    key = rule["key"]
    expected = rule["expected_package_basename"]
    remove_values = set(rule.get("remove_values", ["LastUsedSource"]))

    path = _safe_relative_file(neutral_root, relative)
    before = path.read_bytes()
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImporterError(
            f"{rule_id}: el fichero REG no es UTF-8 legible: {relative}"
        ) from exc

    lines = text.splitlines(keepends=True)
    current: str | None = None
    section_found = False
    package_seen = 0
    removed: list[str] = []
    changed = False
    output: list[str] = []

    for line in lines:
        stripped = line.rstrip("\r\n")
        match = _SECTION_RE.match(stripped.strip())
        if match:
            current = match.group(1)
            if current == key:
                section_found = True
            output.append(line)
            continue

        if current != key:
            output.append(line)
            continue

        value_match = _VALUE_RE.match(stripped.strip())
        if not value_match:
            output.append(line)
            continue

        name, value = value_match.groups()
        if name in remove_values:
            removed.append(name)
            changed = True
            continue

        if name == "PackageName":
            package_seen += 1
            actual_basename = _registry_path_basename(value)
            if actual_basename != expected:
                raise ImporterError(
                    f"{rule_id}: PackageName inesperado: "
                    f"{actual_basename!r} != {expected!r}"
                )
            newline = _line_ending(line)
            replacement = f'"PackageName"="{expected}"{newline}'
            if replacement != line:
                changed = True
            output.append(replacement)
            continue

        output.append(line)

    if not section_found:
        raise ImporterError(f"{rule_id}: no existe la clave REG declarada")
    if package_seen != 1:
        raise ImporterError(
            f"{rule_id}: se esperaba un único PackageName, encontrados {package_seen}"
        )

    after = "".join(output).encode("utf-8")
    if changed:
        temporary = path.with_name(path.name + ".sanitize-part")
        temporary.write_bytes(after)
        os.replace(temporary, path)

    return {
        "id": rule_id,
        "type": "wine-installer-source-list",
        "file": relative,
        "key": key,
        "expected_package_basename": expected,
        "removed_values": sorted(set(removed)),
        "sha256_before": _sha256_bytes(before),
        "sha256_after": _sha256_bytes(after),
        "status": "sanitized" if changed else "already-sanitized",
    }


def validate_sanitization_rules(plan: dict[str, Any]) -> None:
    section = plan.get("privacy_sanitization", {})
    if section in ({}, None):
        return
    if not isinstance(section, dict):
        raise ImporterError("privacy_sanitization debe ser un objeto")
    rules = section.get("wine_installer_source_lists", [])
    if not isinstance(rules, list):
        raise ImporterError(
            "privacy_sanitization.wine_installer_source_lists debe ser una lista"
        )
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ImporterError("cada regla de saneamiento debe ser un objeto")
        for field in ("id", "file", "key", "expected_package_basename"):
            if not isinstance(rule.get(field), str) or not rule[field]:
                raise ImporterError(f"regla de saneamiento sin {field}")
        rule_id = rule["id"]
        if rule_id in seen:
            raise ImporterError(f"id de saneamiento duplicado: {rule_id}")
        seen.add(rule_id)
        pure = PurePosixPath(rule["file"])
        if pure.is_absolute() or ".." in pure.parts:
            raise ImporterError(
                f"{rule_id}: ruta de fichero no relativa: {rule['file']}"
            )
        basename = rule["expected_package_basename"]
        if "/" in basename or "\\" in basename:
            raise ImporterError(
                f"{rule_id}: expected_package_basename no puede contener ruta"
            )
        remove_values = rule.get("remove_values", ["LastUsedSource"])
        if (
            not isinstance(remove_values, list)
            or not all(isinstance(item, str) and item for item in remove_values)
        ):
            raise ImporterError(f"{rule_id}: remove_values inválido")


def apply_sanitization_rules(
    neutral_root: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    validate_sanitization_rules(plan)
    automatic_enabled = plan.get("policy", {}).get(
        "automatic_known_host_path_sanitization",
        True,
    )
    automatic = (
        auto_sanitize_known_host_paths(neutral_root)
        if automatic_enabled
        else {
            "schema": 0,
            "mode": "automatic-known-host-paths-v1",
            "status": "disabled",
            "results": [],
            "files_changed": 0,
            "changes_applied": 0,
            "actions": {},
            "functional_retest_required": False,
        }
    )
    section = plan.get("privacy_sanitization") or {}
    rules = section.get("wine_installer_source_lists", [])
    declared_results = [
        _sanitize_source_list_rule(neutral_root, rule)
        for rule in rules
    ]
    return {
        "schema": 0,
        "automatic": automatic,
        "declared_rules_applied": len(declared_results),
        "declared_results": declared_results,
        "rules_applied": automatic["changes_applied"] + len(declared_results),
        "functional_retest_required": automatic[
            "functional_retest_required"
        ],
        "status": (
            "sanitized"
            if automatic["changes_applied"] or declared_results
            else "not-requested"
        ),
    }
