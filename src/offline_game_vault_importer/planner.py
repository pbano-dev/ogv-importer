from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import re
from typing import Any

from .errors import ImporterError
from .sanitization import validate_sanitization_rules
from .naming import canonical_profile_id, suggest_identifiers

_CAPSULE_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_STATE_ID_RE = _CAPSULE_ID_RE
_ALLOWED_MARKERS = {"[RELLENAR]", "[VERIFICAR]", "[NO PROBADO]", "[NO APLICA]"}


def _first(items: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not items:
        raise ImporterError(f"el escaneo no contiene candidatos para {label}")
    return items[0]


def build_plan(scan: dict[str, Any]) -> dict[str, Any]:
    """Build an editable v2 plan from a legacy package scan.

    Detection is advisory.  Every detected path remains editable and the final
    selection is the authority.
    """
    archives = scan.get("game_archives")
    if not isinstance(archives, list) or len(archives) != 1:
        raise ImporterError("el escaneo debe contener exactamente un archivo jugable")
    archive = archives[0]
    inspection = archive.get("archive_inspection")
    if not isinstance(inspection, dict):
        raise ImporterError(
            "el archivo jugable no fue inspeccionado; repita scan-package "
            "sin --skip-archive-inspection"
        )
    bottle_roots = inspection.get("bottle_root_candidates")
    game_roots = inspection.get("game_root_candidates")
    if not isinstance(bottle_roots, list) or not bottle_roots:
        raise ImporterError("no se detectó una raíz de bottle")
    if not isinstance(game_roots, list) or not game_roots:
        raise ImporterError("no se detectó una raíz de juego")

    bottle_root = _first(bottle_roots, "raíz de bottle")["path"]
    game_candidate = _first(game_roots, "raíz de juego")
    game_root = game_candidate["path"]
    bottle_path = PurePosixPath(bottle_root)
    game_path = PurePosixPath(game_root)

    raw_entrypoints = game_candidate.get("executables", [])
    if not isinstance(raw_entrypoints, list):
        raw_entrypoints = []
    entrypoints: list[str] = []
    for raw in raw_entrypoints:
        try:
            entrypoints.append(
                PurePosixPath(raw).relative_to(game_path).as_posix()
            )
        except ValueError:
            continue

    try:
        game_relative = game_path.relative_to(bottle_path)
    except ValueError as exc:
        raise ImporterError(
            "la raíz de juego candidata no está dentro de la bottle candidata"
        ) from exc

    bottle_metadata: list[str] = []
    for path in inspection.get("bottle_yml_candidates", []):
        try:
            relative = PurePosixPath(path).relative_to(bottle_path)
        except ValueError:
            continue
        bottle_metadata.append(str(relative))

    appid = "[RELLENAR]"
    appmanifests = scan.get("appmanifests", [])
    if isinstance(appmanifests, list) and len(appmanifests) == 1:
        name = PurePosixPath(appmanifests[0]).name
        digits = name.removeprefix("appmanifest_").removesuffix(".acf")
        if digits.isdigit():
            appid = digits

    runners = scan.get("runner_archives", [])
    runner = runners[0] if isinstance(runners, list) and len(runners) == 1 else None

    state_detection = inspection.get("state_path_candidates", [])
    unclassified_paths: set[str] = set()
    if isinstance(state_detection, list):
        for detection in state_detection:
            if not isinstance(detection, dict):
                continue
            for match in detection.get("matches", []):
                if isinstance(match, dict) and isinstance(match.get("path"), str):
                    try:
                        relative = PurePosixPath(match["path"]).relative_to(
                            bottle_path
                        )
                    except ValueError:
                        continue
                    unclassified_paths.add(relative.as_posix())
            for raw in detection.get("related_candidates", []):
                if not isinstance(raw, str):
                    continue
                try:
                    relative = PurePosixPath(raw).relative_to(bottle_path)
                except ValueError:
                    continue
                unclassified_paths.add(relative.as_posix())

    runner_selection: dict[str, Any] = {
        "binding": "select-at-materialization",
        "preferred_id": None,
        "source_path": None,
        "sha256": None,
        "reuse_existing_vault_object": False,
        "vault_matches": [],
        "source_root": None,
        "wine_path": None,
        "wineserver_path": None,
        "compatible_backends": ["direct-wine", "bottles"],
    }
    if isinstance(runner, dict):
        relative = runner.get("relative_path")
        digest = runner.get("computed_sha256") or runner.get("declared_sha256")
        runner_selection.update(
            {
                "binding": "preferred",
                "source_path": relative,
                "sha256": digest,
                "reuse_existing_vault_object": bool(runner.get("known_in_vault")),
                "vault_matches": runner.get("vault_matches", []),
            }
        )
        matches = runner.get("vault_matches")
        if isinstance(matches, list) and len(matches) == 1:
            object_id = matches[0].get("id")
            if isinstance(object_id, str) and object_id:
                runner_selection["preferred_id"] = object_id

    return {
        "schema": 0,
        "contract": "ogv-import-plan-v2",
        "status": "draft",
        "source": {
            "type": "legacy-preservation-package-v1",
            "package_name": scan.get("source_name", "[RELLENAR]"),
            "game_archive": archive.get("relative_path"),
            "game_archive_sha256": (
                archive.get("computed_sha256")
                or archive.get("declared_sha256")
                or "[VERIFICAR]"
            ),
            "bottle_root_in_archive": bottle_root,
        },
        "identity": {
            "title": "[RELLENAR]",
            "edition": "[RELLENAR]",
            "source_store": "Steam",
            "appid": appid,
            "preserved_version": "[RELLENAR]",
            "capsule_id": "[RELLENAR]",
        },
        "layout": {
            "game_root_in_archive": game_root,
            "game_destination_in_prefix": str(game_relative),
            "entrypoint_candidates_relative_to_game": entrypoints,
            "entrypoint": entrypoints[0] if len(entrypoints) == 1 else "[RELLENAR]",
            "working_directory": str(game_relative),
            "bottles_metadata_paths": sorted(set(bottle_metadata)),
        },
        "runner": runner_selection,
        "persistent_state": {
            "items": [],
            "unclassified_candidates": sorted(unclassified_paths),
            "detection_evidence": state_detection,
            "related_file_exceptions": [],
            "require_related_file_closure": False,
            "baseline_state": (
                "embedded-or-unknown" if unclassified_paths else "clean"
            ),
        },
        "supplemental_content": [],
        "privacy_sanitization": {
            "wine_installer_source_lists": [],
        },
        "profiles": [
            {
                "id": "linux-bottles-flatpak",
                "adapter": "bottles",
                "platform": "linux",
                "status": "candidate",
                "enabled": True,
            },
            {
                "id": "linux-direct-wine",
                "adapter": "wine",
                "platform": "linux",
                "status": "candidate",
                "enabled": True,
            },
            {
                "id": "windows-native",
                "adapter": "windows",
                "platform": "windows",
                "status": "not_tested",
                "enabled": True,
            },
        ],
        "policy": {
            "write_vault": False,
            "preserve_source_archive": True,
            "strip_bottles_metadata_from_neutral_object": True,
            "automatic_drm_changes": False,
            "automatic_save_deletion": False,
            "privacy_review_required": True,
            "allow_privacy_pending": False,
            "automatic_known_host_path_sanitization": True,
            "allow_partial_state_classification": True,
        },
        "core": {
            "source_root": None,
            "command": None,
        },
        "acceptance_required": [
            "materialization-clean",
            "launch-without-save",
            "launch-with-selected-save",
            "normal-gameplay",
            "normal-exit",
            "network-policy",
            "state-preservation-on-removal",
            "clean-restoration",
        ],
        "notes": [
            "La detección automática es orientativa; la selección efectiva manda.",
            "Los ficheros de una partida lógica pueden agruparse en un save-set multielemento.",
            "Un runner ausente no bloquea la importación; se seleccionará al materializar.",
            "Los perfiles se publican como candidate/not_tested hasta su aceptación funcional.",
        ],
    }



def _default_profiles() -> list[dict[str, Any]]:
    return [
        {
            "id": canonical_profile_id("bottles"),
            "adapter": "bottles",
            "platform": "linux",
            "status": "candidate",
            "enabled": True,
        },
        {
            "id": canonical_profile_id("wine"),
            "adapter": "wine",
            "platform": "linux",
            "status": "candidate",
            "enabled": False,
        },
        {
            "id": canonical_profile_id("umu"),
            "adapter": "umu",
            "platform": "linux",
            "status": "candidate",
            "enabled": False,
        },
        {
            "id": canonical_profile_id("windows"),
            "adapter": "windows",
            "platform": "windows",
            "status": "not_tested",
            "enabled": False,
        },
    ]


def new_prepared_plan(
    *,
    title: str | None = None,
    game_directory: str | None = None,
) -> dict[str, Any]:
    """Return the single supported import model: an already decoupled game."""
    suggestions = suggest_identifiers(
        title=title,
        game_directory=game_directory,
    )
    display_title = (title or "").strip() or suggestions["basis"]
    capsule_id = suggestions["capsule_id"]
    destination = suggestions["game_destination_in_prefix"]
    return {
        "schema": 0,
        "contract": "ogv-import-plan-v4",
        "status": "draft",
        "source": {
            "type": "prepared-offline-game-directory-v1",
            "game_directory": game_directory,
            "steam_independent": True,
            "legitimate_source": True,
            "third_party_drm": "declared-absent",
            "preparation": {
                "steamworks": "gbe_fork-or-goldberg-or-not-required",
                "steamstub": "steamless-or-not-required",
                "performed_before_import": True,
            },
        },
        "identity": {
            "title": display_title,
            "edition": "Standard",
            "source_store": "Steam",
            "appid": None,
            "preserved_version": "[RELLENAR]",
            "capsule_id": capsule_id,
        },
        "layout": {
            "game_root_in_archive": None,
            "game_destination_in_prefix": destination,
            "entrypoint_candidates_relative_to_game": [],
            "entrypoint": "[RELLENAR]",
            "working_directory": destination,
            "bottles_metadata_paths": [],
        },
        "runner": {
            "binding": "select-at-materialization",
            "preferred_id": None,
            "source_path": None,
            "sha256": None,
            "reuse_existing_vault_object": False,
            "vault_matches": [],
            "source_root": None,
            "wine_path": None,
            "wineserver_path": None,
            "compatible_backends": ["direct-wine", "bottles", "umu"],
        },
        "persistent_state": {
            "items": [],
            "unclassified_candidates": [],
            "detection_evidence": [],
            "related_file_exceptions": [],
            "require_related_file_closure": False,
            "baseline_state": "embedded-or-unknown",
        },
        "supplemental_content": [],
        "documentation": [],
        "privacy_sanitization": {"wine_installer_source_lists": []},
        "profiles": [
            {
                "id": "game-source",
                "adapter": "bottles",
                "platform": "linux",
                "status": "candidate",
                "enabled": True,
            }
        ],
        "policy": {
            "write_vault": False,
            "preserve_source_archive": False,
            "strip_bottles_metadata_from_neutral_object": False,
            "automatic_drm_changes": False,
            "automatic_save_deletion": False,
            "privacy_review_required": True,
            "allow_privacy_pending": False,
            "automatic_known_host_path_sanitization": True,
            "allow_partial_state_classification": True,
        },
        "core": {"source_root": None, "command": None},
        "acceptance_required": [
            "materialization-clean",
            "launch-without-steam",
            "launch-with-selected-save",
            "normal-gameplay",
            "normal-exit",
            "network-policy",
            "state-preservation-on-removal",
            "clean-restoration",
        ],
        "naming_examples": suggestions,
        "notes": [
            "El único origen admitido es un directorio jugable ya desacoplado de Steam.",
            (
                "source.game_directory apunta a la carpeta que contiene los "
                "binarios del juego; drive_c es topología de materialización, "
                "no el objeto de juego que se importa por defecto."
            ),
            "El importador no aplica Steamless ni sustituye DLLs de Steamworks.",
            "La selección explícita del usuario es la autoridad.",
            "La importación estructural no concede aceptación funcional.",
        ],
    }


def new_manual_plan() -> dict[str, Any]:
    """Compatibility alias for 0.2.x callers; returns the v3 prepared-game plan."""
    return new_prepared_plan()


def require_prepared_plan_contract(plan: dict[str, Any]) -> None:
    """Reject every public import contract except the prepared-game v3 model."""
    if not isinstance(plan, dict) or plan.get("contract") != "ogv-import-plan-v4":
        raise ImporterError(
            "solo se admite contract=ogv-import-plan-v4; "
            "los planes históricos ya no forman parte del flujo público"
        )
    source = plan.get("source")
    if not isinstance(source, dict) or source.get("type") != (
        "prepared-offline-game-directory-v1"
    ):
        raise ImporterError(
            "solo se admite source.type="
            "prepared-offline-game-directory-v1"
        )

def unresolved_markers(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(unresolved_markers(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(unresolved_markers(child, f"{path}[{index}]"))
    elif isinstance(value, str) and value in _ALLOWED_MARKERS:
        found.append(path)
    return found


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ImporterError(f"{label} no es una ruta relativa portable")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ImporterError(f"{label} no es una ruta relativa segura")
    return path.as_posix()


def upgrade_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return an in-memory v3 plan while preserving earlier explicit selections."""
    if plan.get("contract") == "ogv-import-plan-v4":
        result = deepcopy(plan)
    elif plan.get("contract") == "ogv-import-plan-v3":
        result = deepcopy(plan)
        result["contract"] = "ogv-import-plan-v4"
    elif plan.get("contract") in {"ogv-import-plan-v2", "ogv-neutral-import-plan-v1"}:
        result = deepcopy(plan)
        result["contract"] = "ogv-import-plan-v4"
        old_runner = result.get("runner")
        if isinstance(old_runner, dict) and "binding" not in old_runner:
            matches = old_runner.get("vault_matches")
            preferred = None
            if isinstance(matches, list) and len(matches) == 1:
                candidate = matches[0].get("id")
                if isinstance(candidate, str) and candidate:
                    preferred = candidate
            result["runner"] = {
                "binding": (
                    "preferred"
                    if old_runner.get("relative_path")
                    not in {None, "", "[RELLENAR]"}
                    else "select-at-materialization"
                ),
                "preferred_id": preferred,
                "source_path": (
                    None
                    if old_runner.get("relative_path") in {None, "", "[RELLENAR]"}
                    else old_runner.get("relative_path")
                ),
                "sha256": (
                    None
                    if old_runner.get("sha256") in {None, "", "[VERIFICAR]"}
                    else old_runner.get("sha256")
                ),
                "reuse_existing_vault_object": bool(
                    old_runner.get("reuse_existing_vault_object")
                ),
                "vault_matches": old_runner.get("vault_matches", []),
                "source_root": None,
                "wine_path": None,
                "wineserver_path": None,
                "compatible_backends": ["direct-wine", "bottles"],
            }
        result.setdefault("supplemental_content", [])
        result.setdefault("core", {"source_root": None, "command": None})
        policy = result.setdefault("policy", {})
        policy.setdefault("allow_partial_state_classification", True)
        policy.setdefault("allow_privacy_pending", False)
        state = result.setdefault("persistent_state", {})
        state.setdefault(
            "baseline_state",
            (
                "embedded-or-unknown"
                if state.get("unclassified_candidates")
                else "clean"
            ),
        )
        for profile in result.get("profiles", []):
            if not isinstance(profile, dict):
                continue
            adapter = profile.get("adapter")
            profile.setdefault(
                "platform", "windows" if adapter == "windows" else "linux"
            )
    else:
        raise ImporterError("contrato de plan no admitido")

    result.setdefault("documentation", [])
    result.setdefault("supplemental_content", [])
    result.setdefault("privacy_sanitization", {"wine_installer_source_lists": []})
    result.setdefault("core", {"source_root": None, "command": None})
    result.setdefault("naming_examples", suggest_identifiers(
        title=result.get("identity", {}).get("title"),
        game_directory=result.get("source", {}).get("game_directory"),
    ))
    source = result.setdefault("source", {})
    if source.get("type") in {"manual-components-v1", "legacy-preservation-package-v1"}:
        source["legacy_source_type"] = source.get("type")
        source["type"] = "prepared-offline-game-directory-v1"
    source.setdefault("steam_independent", True)
    source.setdefault("legitimate_source", True)
    source.setdefault("third_party_drm", "declared-absent")
    source.setdefault(
        "preparation",
        {
            "steamworks": "declared-prepared-or-not-required",
            "steamstub": "declared-prepared-or-not-required",
            "performed_before_import": True,
        },
    )
    return result


def _validate_identity(plan: dict[str, Any], *, for_commit: bool) -> None:
    identity = plan.get("identity")
    if not isinstance(identity, dict):
        raise ImporterError("identity inválido")
    required = ("title", "source_store", "preserved_version", "capsule_id")
    for key in required:
        value = identity.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ImporterError(f"identity.{key} debe contener texto")
        if for_commit and value in _ALLOWED_MARKERS:
            raise ImporterError(f"identity.{key} sigue pendiente")
    capsule_id = identity.get("capsule_id")
    if (
        for_commit
        and isinstance(capsule_id, str)
        and not _CAPSULE_ID_RE.fullmatch(capsule_id)
    ):
        raise ImporterError("identity.capsule_id no es portable")


def _validate_layout(plan: dict[str, Any], *, for_commit: bool) -> None:
    layout = plan.get("layout")
    if not isinstance(layout, dict):
        raise ImporterError("layout inválido")
    for key in ("game_destination_in_prefix", "working_directory"):
        value = layout.get(key)
        if value in _ALLOWED_MARKERS and not for_commit:
            continue
        _safe_relative(value, f"layout.{key}")
    entrypoint = layout.get("entrypoint")
    if entrypoint in _ALLOWED_MARKERS and not for_commit:
        return
    _safe_relative(entrypoint, "layout.entrypoint")


def _validate_state(plan: dict[str, Any], *, for_prepare: bool) -> None:
    state = plan.get("persistent_state")
    if not isinstance(state, dict):
        raise ImporterError("persistent_state inválido")
    unclassified = state.get("unclassified_candidates", [])
    if not isinstance(unclassified, list):
        raise ImporterError("unclassified_candidates debe ser una lista")
    items = state.get("items")
    if not isinstance(items, list):
        raise ImporterError("persistent_state.items debe ser una lista")

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ImporterError("cada estado debe ser un objeto")
        state_id = item.get("id")
        if not isinstance(state_id, str) or not _STATE_ID_RE.fullmatch(state_id):
            raise ImporterError("estado sin id portable")
        if state_id in seen_ids:
            raise ImporterError(f"id de estado duplicado: {state_id}")
        seen_ids.add(state_id)

        path = item.get("path")
        destination_bound = path not in {None, "", "[RELLENAR]"}
        if destination_bound:
            normalized = _safe_relative(path, f"estado {state_id}.path")
            if normalized in seen_paths:
                raise ImporterError(f"ruta de estado duplicada: {normalized}")
            seen_paths.add(normalized)
        elif item.get("disposition") != "unbound":
            raise ImporterError(
                f"estado {state_id} sin destino debe usar disposition=unbound"
            )

        disposition = item.get("disposition")
        if disposition not in (
            "save-set",
            "identity",
            "configuration",
            "exclude",
            "embedded",
            "unbound",
        ):
            raise ImporterError(
                f"disposición no admitida para {state_id}: {disposition}"
            )
        if disposition == "save-set":
            save_set = item.get("save_set_id")
            if not isinstance(save_set, str) or not _STATE_ID_RE.fullmatch(save_set):
                raise ImporterError(
                    f"estado {state_id} requiere save_set_id portable"
                )
        source_path = item.get("source_path")
        workspace_path = item.get("workspace_path")
        if source_path is not None and not isinstance(source_path, str):
            raise ImporterError(f"estado {state_id}.source_path inválido")
        if workspace_path is not None:
            _safe_relative(workspace_path, f"estado {state_id}.workspace_path")

    require_closure = bool(state.get("require_related_file_closure"))
    if (
        for_prepare
        and require_closure
        and unclassified
        and not plan.get("policy", {}).get(
            "allow_partial_state_classification", False
        )
    ):
        raise ImporterError(
            "quedan candidatos de estado sin clasificar: "
            + ", ".join(str(item) for item in unclassified)
        )


def _validate_source(plan: dict[str, Any], *, for_commit: bool) -> None:
    source = plan.get("source")
    if not isinstance(source, dict):
        raise ImporterError("source inválido")
    if source.get("type") != "prepared-offline-game-directory-v1":
        raise ImporterError(
            "solo se admite source.type=prepared-offline-game-directory-v1"
        )
    for key in ("steam_independent", "legitimate_source"):
        if source.get(key) is not True:
            raise ImporterError(f"source.{key} debe confirmarse")
    if source.get("third_party_drm") != "declared-absent":
        raise ImporterError(
            "el flujo preparado requiere third_party_drm=declared-absent"
        )
    preparation = source.get("preparation")
    if not isinstance(preparation, dict) or preparation.get(
        "performed_before_import"
    ) is not True:
        raise ImporterError(
            "source.preparation.performed_before_import debe ser true"
        )


def _validate_profiles(plan: dict[str, Any]) -> None:
    profiles = plan.get("profiles")
    if not isinstance(profiles, list):
        raise ImporterError("profiles debe ser una lista")
    seen: set[str] = set()
    enabled = 0
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ImporterError("perfil inválido")
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not _CAPSULE_ID_RE.fullmatch(
            profile_id
        ):
            raise ImporterError("profile.id no es portable")
        if profile_id in seen:
            raise ImporterError(f"profile.id duplicado: {profile_id}")
        seen.add(profile_id)
        if profile.get("enabled") is True:
            enabled += 1
        adapter = profile.get("adapter")
        if adapter not in {"bottles", "wine", "umu", "windows", "other"}:
            raise ImporterError(f"adaptador de perfil no admitido: {adapter}")
        status = profile.get("status")
        if status == "verified":
            raise ImporterError(
                "el importer no puede publicar perfiles verified"
            )
        if status not in {
            "candidate",
            "experimental",
            "not_tested",
            "unavailable",
        }:
            raise ImporterError(f"estado de perfil no admitido: {status}")
    if enabled == 0:
        raise ImporterError("el plan debe habilitar al menos un perfil")


def _validate_attachments(plan: dict[str, Any]) -> None:
    for key in ("supplemental_content", "documentation"):
        values = plan.get(key, [])
        if not isinstance(values, list):
            raise ImporterError(f"{key} debe ser una lista")
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                raise ImporterError(f"{key} contiene una entrada inválida")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not _CAPSULE_ID_RE.fullmatch(
                item_id
            ):
                raise ImporterError(f"{key}.id no es portable")
            if item_id in seen:
                raise ImporterError(f"{key}.id duplicado: {item_id}")
            seen.add(item_id)
            source_path = item.get("source_path")
            if source_path is not None and not isinstance(source_path, str):
                raise ImporterError(f"{key}.{item_id}.source_path inválido")
            workspace_path = item.get("workspace_path")
            if workspace_path is not None:
                _safe_relative(
                    workspace_path, f"{key}.{item_id}.workspace_path"
                )
    canonical_names: set[str] = set()
    for item in plan.get("documentation", []):
        canonical = item.get("canonical_name")
        if not isinstance(canonical, str) or not canonical:
            raise ImporterError("documentation.canonical_name es obligatorio")
        if "/" in canonical or "\\" in canonical or canonical in {".", ".."}:
            raise ImporterError("documentation.canonical_name no es portable")
        if canonical in canonical_names:
            raise ImporterError(
                f"nombre documental canónico duplicado: {canonical}"
            )
        canonical_names.add(canonical)
        role = item.get("role")
        if role not in {
            "readme",
            "game_sheet",
            "credits",
            "preserved_by",
            "technical_notes",
            "addendum",
            "other",
        }:
            raise ImporterError(f"rol documental no admitido: {role}")


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a publication-safe copy without host-local source paths."""
    result = deepcopy(plan)
    source = result.get("source")
    if isinstance(source, dict):
        for key in (
            "game_directory",
            "prefix_directory",
            "source_package",
            "game_archive",
        ):
            if key in source:
                source[key] = None
    runner = result.get("runner")
    if isinstance(runner, dict):
        for key in ("source_path", "source_root"):
            if key in runner:
                runner[key] = None
    for section in ("persistent_state",):
        value = result.get(section)
        if not isinstance(value, dict):
            continue
        for item in value.get("items", []):
            if isinstance(item, dict):
                item["source_path"] = None
    for section in ("supplemental_content", "documentation"):
        for item in result.get(section, []):
            if isinstance(item, dict):
                item["source_path"] = None
    core = result.get("core")
    if isinstance(core, dict):
        core["source_root"] = None
        core["command"] = None
    result["publication_redaction"] = {
        "host_source_paths": "removed",
        "core_command": "removed",
    }
    return result

def validate_plan(
    plan: dict[str, Any],
    *,
    phase: str = "prepare",
) -> dict[str, Any]:
    """Validate and return a normalized v3 plan.

    ``prepare`` accepts unresolved state classification and an unbound runner.
    ``commit`` additionally requires the identity and entrypoint needed to
    publish a schema-valid candidate capsule.
    """
    normalized = upgrade_plan(plan)
    if normalized.get("schema") != 0:
        raise ImporterError("schema de plan no admitido")
    if phase not in {"prepare", "commit", "gui"}:
        raise ImporterError(f"fase de validación desconocida: {phase}")

    validate_sanitization_rules(normalized)
    _validate_identity(normalized, for_commit=phase == "commit")
    _validate_layout(normalized, for_commit=phase == "commit")
    _validate_state(normalized, for_prepare=phase == "prepare")

    _validate_source(normalized, for_commit=phase == "commit")
    _validate_profiles(normalized)
    _validate_attachments(normalized)

    runner = normalized.get("runner")
    if not isinstance(runner, dict):
        raise ImporterError("runner inválido")
    binding = runner.get("binding", "select-at-materialization")
    if binding not in {
        "select-at-materialization",
        "preferred",
        "fixed",
        "none",
    }:
        raise ImporterError("runner.binding no admitido")

    if phase == "commit":
        markers = [
            path
            for path in unresolved_markers(normalized)
            if not (
                path.startswith("$.runner.")
                or "unclassified_candidates" in path
                or path.startswith("$.notes")
                or path.startswith("$.acceptance_required")
            )
        ]
        if markers:
            raise ImporterError(
                "el plan contiene marcadores requeridos sin resolver: "
                + ", ".join(markers)
            )
    return normalized
