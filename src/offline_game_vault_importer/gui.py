from __future__ import annotations

import json
from pathlib import Path
import threading
import traceback
from typing import Any

from .errors import ImporterError
from .gui_model import ImportSession
from .planner import new_manual_plan
from .util import write_json

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk
except (ImportError, ValueError):  # CLI and tests remain usable without GTK.
    Gio = GLib = Gtk = None


def _require_gtk() -> None:
    if Gtk is None:
        raise ImporterError(
            "La GUI requiere PyGObject/GTK 4. Use la CLI ogv-import "
            "si GTK no está disponible."
        )


if Gtk is not None:
    class ImporterWindow(Gtk.ApplicationWindow):
        def __init__(self, application: Gtk.Application):
            super().__init__(
                application=application,
                title="OfflineGameVault Importer 0.2.1",
            )
            self.set_default_size(1040, 760)
            self.session = ImportSession()
            self.entries: dict[str, Gtk.Entry] = {}
            self._build()

        def _build(self) -> None:
            root = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
            )
            root.set_margin_top(12)
            root.set_margin_bottom(12)
            root.set_margin_start(12)
            root.set_margin_end(12)
            self.set_child(root)

            header = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            title = Gtk.Label()
            title.set_markup(
                "<b>Importar un juego como candidato</b>\n"
                "<small>La detección propone; la selección efectiva manda.</small>"
            )
            title.set_xalign(0)
            title.set_hexpand(True)
            header.append(title)
            new_button = Gtk.Button(label="Nuevo plan manual")
            new_button.connect("clicked", self._new_manual)
            header.append(new_button)
            load_button = Gtk.Button(label="Cargar plan…")
            load_button.connect("clicked", self._load_plan_dialog)
            header.append(load_button)
            root.append(header)

            self.notebook = Gtk.Notebook()
            self.notebook.set_vexpand(True)
            root.append(self.notebook)

            self._build_paths_page()
            self._build_identity_page()
            self._build_components_page()
            self._build_state_page()
            self._build_profiles_page()
            self._build_commit_page()

            status_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=4,
            )
            self.status = Gtk.Label(label="Sin plan cargado.")
            self.status.set_xalign(0)
            self.status.set_wrap(True)
            status_box.append(self.status)
            self.progress = Gtk.ProgressBar()
            self.progress.set_visible(False)
            status_box.append(self.progress)
            root.append(status_box)

        def _page(self, title: str) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=10,
            )
            box.set_margin_top(12)
            box.set_margin_bottom(12)
            box.set_margin_start(12)
            box.set_margin_end(12)
            scroller.set_child(box)
            self.notebook.append_page(scroller, Gtk.Label(label=title))
            return scroller, box

        def _path_row(
            self,
            box: Gtk.Box,
            key: str,
            label: str,
            *,
            folder: bool | None,
        ) -> None:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            text.set_size_request(180, -1)
            row.append(text)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            self.entries[key] = entry
            row.append(entry)
            if folder is None:
                file_button = Gtk.Button(label="Archivo…")
                file_button.connect(
                    "clicked",
                    lambda _button: self._choose_path(entry, folder=False),
                )
                row.append(file_button)
                folder_button = Gtk.Button(label="Directorio…")
                folder_button.connect(
                    "clicked",
                    lambda _button: self._choose_path(entry, folder=True),
                )
                row.append(folder_button)
            else:
                button = Gtk.Button(label="Seleccionar…")
                button.connect(
                    "clicked",
                    lambda _button: self._choose_path(entry, folder=folder),
                )
                row.append(button)
            box.append(row)

        def _field(
            self,
            grid: Gtk.Grid,
            row: int,
            key: str,
            label: str,
        ) -> None:
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            grid.attach(text, 0, row, 1, 1)
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            self.entries[key] = entry
            grid.attach(entry, 1, row, 1, 1)

        def _build_paths_page(self) -> None:
            _, box = self._page("Origen")
            note = Gtk.Label(
                label=(
                    "Puede trabajar desde un paquete histórico o señalar "
                    "manualmente el juego y el prefix. El runner es opcional."
                )
            )
            note.set_wrap(True)
            note.set_xalign(0)
            box.append(note)
            self._path_row(box, "vault", "Vault de destino", folder=True)
            self._path_row(box, "workspace", "Workspace", folder=True)
            self._path_row(
                box, "source_package", "Paquete histórico", folder=True
            )
            self._path_row(box, "manual_game", "Juego (manual)", folder=True)
            self._path_row(box, "manual_prefix", "Prefix (manual)", folder=True)
            self._path_row(box, "core_root", "Checkout del núcleo", folder=True)

            actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            scan = Gtk.Button(label="Escanear paquete")
            scan.connect("clicked", self._scan_package)
            actions.append(scan)
            prepare = Gtk.Button(label="Preparar paquete")
            prepare.connect("clicked", self._prepare_legacy)
            actions.append(prepare)
            manual = Gtk.Button(label="Preparar selección manual")
            manual.connect("clicked", self._prepare_manual)
            actions.append(manual)
            box.append(actions)

        def _build_identity_page(self) -> None:
            _, box = self._page("Identidad")
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            box.append(grid)
            for row, (key, label) in enumerate(
                (
                    ("title", "Título"),
                    ("edition", "Edición"),
                    ("store", "Tienda"),
                    ("appid", "AppID"),
                    ("version", "Versión preservada"),
                    ("capsule_id", "Capsule ID"),
                    ("entrypoint", "Ejecutable relativo"),
                    ("game_destination", "Destino dentro del prefix"),
                    ("working_directory", "Directorio de trabajo"),
                )
            ):
                self._field(grid, row, key, label)

            save = Gtk.Button(label="Aplicar identidad y layout")
            save.connect("clicked", self._apply_identity)
            box.append(save)

        def _build_components_page(self) -> None:
            _, box = self._page("Componentes")
            runner_frame = Gtk.Frame(label="Runner o runtime")
            runner_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            runner_box.set_margin_top(8)
            runner_box.set_margin_bottom(8)
            runner_box.set_margin_start(8)
            runner_box.set_margin_end(8)
            runner_frame.set_child(runner_box)
            box.append(runner_frame)

            binding_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            binding_row.append(Gtk.Label(label="Vinculación"))
            self.runner_binding = Gtk.DropDown.new_from_strings(
                [
                    "select-at-materialization",
                    "preferred",
                    "fixed",
                    "none",
                ]
            )
            binding_row.append(self.runner_binding)
            runner_box.append(binding_row)
            self._path_row(
                runner_box, "runner_source", "Runner (archivo o directorio)", folder=None
            )
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            runner_box.append(grid)
            self._field(grid, 0, "runner_id", "ID sugerido")
            self._field(grid, 1, "runner_sha256", "SHA-256 (opcional)")
            runner_apply = Gtk.Button(label="Aplicar runner")
            runner_apply.connect("clicked", self._apply_runner)
            runner_box.append(runner_apply)

            extra = Gtk.Frame(label="Contenido adicional")
            extra_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            extra_box.set_margin_top(8)
            extra_box.set_margin_bottom(8)
            extra_box.set_margin_start(8)
            extra_box.set_margin_end(8)
            extra.set_child(extra_box)
            box.append(extra)
            self._path_row(
                extra_box, "supplemental_source", "Archivo o directorio", folder=None
            )
            extra_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            extra_box.append(extra_grid)
            self._field(extra_grid, 0, "supplemental_id", "ID")
            self._field(
                extra_grid, 1, "supplemental_class", "Clasificación"
            )
            add = Gtk.Button(label="Añadir contenido")
            add.connect("clicked", self._add_supplemental)
            extra_box.append(add)
            self.supplemental_list = Gtk.Label(label="Sin contenido declarado.")
            self.supplemental_list.set_xalign(0)
            self.supplemental_list.set_wrap(True)
            extra_box.append(self.supplemental_list)

        def _build_state_page(self) -> None:
            _, box = self._page("Partidas y estado")
            note = Gtk.Label(
                label=(
                    "Cada fichero o directorio puede seleccionarse manualmente. "
                    "Varios elementos con el mismo save-set forman una partida "
                    "atómica."
                )
            )
            note.set_wrap(True)
            note.set_xalign(0)
            box.append(note)

            baseline_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            baseline_row.append(Gtk.Label(label="Estado del baseline"))
            self.baseline_state = Gtk.DropDown.new_from_strings(
                ["embedded-or-unknown", "clean"]
            )
            baseline_row.append(self.baseline_state)
            box.append(baseline_row)

            self._path_row(
                box, "state_source", "Fuente del estado", folder=None
            )
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            box.append(grid)
            self._field(grid, 0, "state_id", "ID del estado")
            self._field(grid, 1, "state_destination", "Destino relativo")
            self._field(grid, 2, "save_set_id", "Save-set")
            self._field(grid, 3, "save_display", "Nombre visible")

            disposition_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            disposition_row.append(Gtk.Label(label="Disposición"))
            self.state_disposition = Gtk.DropDown.new_from_strings(
                [
                    "save-set",
                    "identity",
                    "configuration",
                    "exclude",
                    "embedded",
                    "unbound",
                ]
            )
            disposition_row.append(self.state_disposition)
            box.append(disposition_row)

            add = Gtk.Button(label="Añadir estado")
            add.connect("clicked", self._add_state)
            box.append(add)
            self.state_list = Gtk.Label(label="Sin estado declarado.")
            self.state_list.set_xalign(0)
            self.state_list.set_wrap(True)
            self.state_list.set_selectable(True)
            box.append(self.state_list)

        def _build_profiles_page(self) -> None:
            _, box = self._page("Perfiles")
            self.profile_checks: dict[str, Gtk.CheckButton] = {}
            for profile_id, label in (
                ("linux-bottles-flatpak", "Bottles — candidate"),
                ("linux-direct-wine", "Direct-Wine — candidate"),
                ("windows-native", "Windows — not_tested"),
            ):
                check = Gtk.CheckButton(label=label)
                check.set_active(True)
                self.profile_checks[profile_id] = check
                box.append(check)
            note = Gtk.Label(
                label=(
                    "Un perfil candidato puede materializarse para probarlo. "
                    "No se declara verificado durante la importación."
                )
            )
            note.set_xalign(0)
            note.set_wrap(True)
            box.append(note)
            apply_button = Gtk.Button(label="Aplicar perfiles")
            apply_button.connect("clicked", self._apply_profiles)
            box.append(apply_button)

        def _build_commit_page(self) -> None:
            _, box = self._page("Validar e importar")
            self.summary = Gtk.Label(
                label="Cargue o cree un plan para ver el resumen."
            )
            self.summary.set_xalign(0)
            self.summary.set_wrap(True)
            self.summary.set_selectable(True)
            box.append(self.summary)
            actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            validate = Gtk.Button(label="Validar selección")
            validate.connect("clicked", self._validate)
            actions.append(validate)
            self.verify_button = Gtk.Button(label="Verificar workspace")
            self.verify_button.connect("clicked", self._verify_workspace)
            self.verify_button.set_sensitive(False)
            actions.append(self.verify_button)
            self.dry_button = Gtk.Button(label="Ensayo de importación")
            self.dry_button.connect(
                "clicked", lambda _b: self._commit(dry_run=True)
            )
            self.dry_button.set_sensitive(False)
            actions.append(self.dry_button)
            self.commit_button = Gtk.Button(label="Importar al Vault")
            self.commit_button.add_css_class("suggested-action")
            self.commit_button.connect(
                "clicked", lambda _b: self._commit(dry_run=False)
            )
            self.commit_button.set_sensitive(False)
            actions.append(self.commit_button)
            box.append(actions)

        def _choose_path(self, entry: Gtk.Entry, *, folder: bool) -> None:
            dialog = Gtk.FileChooserNative(
                title="Seleccionar",
                transient_for=self,
                action=(
                    Gtk.FileChooserAction.SELECT_FOLDER
                    if folder
                    else Gtk.FileChooserAction.OPEN
                ),
                accept_label="Seleccionar",
                cancel_label="Cancelar",
            )
            def response(native: Gtk.FileChooserNative, response_id: int) -> None:
                if response_id == Gtk.ResponseType.ACCEPT:
                    selected = native.get_file()
                    if selected is not None and selected.get_path():
                        entry.set_text(selected.get_path())
                native.destroy()
            dialog.connect("response", response)
            dialog.show()

        def _set_busy(self, busy: bool, message: str = "") -> None:
            self.progress.set_visible(busy)
            if busy:
                self.progress.pulse()
            if message:
                self.status.set_text(message)

        def _run_background(self, label: str, function) -> None:
            self._set_busy(True, label)
            def worker() -> None:
                try:
                    result = function()
                except Exception as exc:
                    detail = f"{exc}"
                    GLib.idle_add(self._operation_failed, label, detail)
                else:
                    GLib.idle_add(self._operation_finished, label, result)
            threading.Thread(target=worker, daemon=True).start()

        def _operation_failed(self, label: str, detail: str) -> bool:
            self._set_busy(False)
            self.status.set_text(f"{label}: ERROR — {detail}")
            return False

        def _operation_finished(self, label: str, result: Any) -> bool:
            self._set_busy(False)
            rendered = json.dumps(
                result, ensure_ascii=False, indent=2, sort_keys=True
            )
            self.status.set_text(f"{label}: completado")
            self.summary.set_text(rendered)
            if label == "Escaneo":
                # Load the automatically proposed AppID, layout and runner into
                # the editable fields. Detection remains advisory: the user can
                # replace every value before preparing the workspace.
                self._populate()
            else:
                self._refresh()
            return False

        def _entry(self, key: str) -> str:
            return self.entries[key].get_text().strip()

        def _new_manual(self, _button: Gtk.Button) -> None:
            self.session.plan = new_manual_plan()
            self.session.plan_path = None
            self._populate()
            self.status.set_text("Plan manual nuevo.")

        def _load_plan_dialog(self, _button: Gtk.Button) -> None:
            dialog = Gtk.FileChooserNative(
                title="Cargar IMPORT_PLAN.json",
                transient_for=self,
                action=Gtk.FileChooserAction.OPEN,
                accept_label="Cargar",
                cancel_label="Cancelar",
            )
            def response(native, response_id):
                if response_id == Gtk.ResponseType.ACCEPT:
                    selected = native.get_file()
                    try:
                        path = Path(selected.get_path())
                        self.session.load_plan(path)
                        if path.name == "IMPORT_PLAN.json":
                            self.session.workspace = path.parent
                            self.entries["workspace"].set_text(str(path.parent))
                        self._populate()
                    except Exception as exc:
                        self.status.set_text(f"Error al cargar: {exc}")
                native.destroy()
            dialog.connect("response", response)
            dialog.show()

        def _populate(self) -> None:
            plan = self.session.plan
            if plan is None:
                return
            identity = plan["identity"]
            layout = plan["layout"]
            values = {
                "title": identity.get("title", ""),
                "edition": identity.get("edition", ""),
                "store": identity.get("source_store", ""),
                "appid": str(identity.get("appid", "")),
                "version": identity.get("preserved_version", ""),
                "capsule_id": identity.get("capsule_id", ""),
                "entrypoint": layout.get("entrypoint", ""),
                "game_destination": layout.get(
                    "game_destination_in_prefix", ""
                ),
                "working_directory": layout.get("working_directory", ""),
                "runner_source": plan["runner"].get("source_path") or "",
                "runner_id": plan["runner"].get("preferred_id") or "",
                "runner_sha256": plan["runner"].get("sha256") or "",
            }
            for key, value in values.items():
                if key in self.entries:
                    self.entries[key].set_text(str(value))
            bindings = [
                "select-at-materialization", "preferred", "fixed", "none"
            ]
            try:
                self.runner_binding.set_selected(
                    bindings.index(plan["runner"].get("binding"))
                )
            except ValueError:
                self.runner_binding.set_selected(0)
            baseline_values = ["embedded-or-unknown", "clean"]
            try:
                self.baseline_state.set_selected(
                    baseline_values.index(
                        plan["persistent_state"].get(
                            "baseline_state", "embedded-or-unknown"
                        )
                    )
                )
            except ValueError:
                self.baseline_state.set_selected(0)
            core_root = plan.get("core", {}).get("source_root")
            if core_root:
                self.entries["core_root"].set_text(core_root)
            self._refresh()

        def _sync_paths(self) -> None:
            vault = self._entry("vault")
            workspace = self._entry("workspace")
            source = self._entry("source_package")
            if vault:
                self.session.vault = Path(vault)
            if workspace:
                self.session.workspace = Path(workspace)
            if source:
                self.session.source_package = Path(source)
            if self.session.plan is not None:
                core = self._entry("core_root")
                self.session.plan.setdefault("core", {})["source_root"] = (
                    core or None
                )
                baseline_values = ["embedded-or-unknown", "clean"]
                selected = int(self.baseline_state.get_selected())
                if selected < 0 or selected >= len(baseline_values):
                    selected = 0
                self.session.plan["persistent_state"]["baseline_state"] = (
                    baseline_values[selected]
                )

        def _apply_identity(self, _button=None) -> None:
            try:
                self.session.set_identity(
                    title=self._entry("title"),
                    edition=self._entry("edition"),
                    store=self._entry("store"),
                    appid=self._entry("appid"),
                    version=self._entry("version"),
                    capsule_id=self._entry("capsule_id"),
                )
                self.session.set_layout(
                    entrypoint=self._entry("entrypoint"),
                    game_destination=self._entry("game_destination"),
                    working_directory=self._entry("working_directory"),
                )
                self._sync_paths()
                self.status.set_text("Identidad y layout aplicados.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _apply_runner(self, _button=None) -> None:
            try:
                bindings = [
                    "select-at-materialization", "preferred", "fixed", "none"
                ]
                self.session.set_runner(
                    binding=bindings[self.runner_binding.get_selected()],
                    source_path=self._entry("runner_source") or None,
                    preferred_id=self._entry("runner_id") or None,
                    digest=self._entry("runner_sha256") or None,
                )
                self.status.set_text("Runner aplicado.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _add_state(self, _button=None) -> None:
            try:
                dispositions = [
                    "save-set", "identity", "configuration",
                    "exclude", "embedded", "unbound",
                ]
                disposition = dispositions[
                    self.state_disposition.get_selected()
                ]
                kind = (
                    "save" if disposition == "save-set"
                    else (
                        disposition
                        if disposition in {"identity", "configuration"}
                        else "other"
                    )
                )
                self.session.add_state(
                    state_id=self._entry("state_id"),
                    source_path=self._entry("state_source") or None,
                    destination_path=self._entry("state_destination") or None,
                    disposition=disposition,
                    kind=kind,
                    save_set_id=self._entry("save_set_id") or None,
                    display_name=self._entry("save_display") or None,
                )
                for key in (
                    "state_id", "state_source", "state_destination",
                    "save_set_id", "save_display",
                ):
                    self.entries[key].set_text("")
                if (
                    self.session.plan["persistent_state"].get("baseline_state")
                    == "clean"
                ):
                    self.baseline_state.set_selected(1)
                self.status.set_text("Estado añadido.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _add_supplemental(self, _button=None) -> None:
            try:
                self.session.add_supplemental(
                    item_id=self._entry("supplemental_id"),
                    source_path=self._entry("supplemental_source") or None,
                    classification=(
                        self._entry("supplemental_class")
                        or "supplemental-content"
                    ),
                )
                self.entries["supplemental_id"].set_text("")
                self.entries["supplemental_source"].set_text("")
                self.status.set_text("Contenido adicional añadido.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _apply_profiles(self, _button=None) -> None:
            if self.session.plan is None:
                return
            for profile in self.session.plan["profiles"]:
                check = self.profile_checks.get(profile["id"])
                if check is not None:
                    profile["enabled"] = check.get_active()
            self.status.set_text("Perfiles aplicados.")
            self._refresh()

        def _update_action_sensitivity(self) -> None:
            workspace = self.session.workspace
            prepared = bool(
                workspace is not None
                and workspace.is_dir()
                and (workspace / "PREPARE_RECEIPT.json").is_file()
                and (workspace / "IMPORT_PLAN.json").is_file()
                and (workspace / "objects/neutral-game.tar.gz").is_file()
            )
            self.verify_button.set_sensitive(prepared)
            self.dry_button.set_sensitive(prepared)
            self.commit_button.set_sensitive(prepared)

        def _refresh(self) -> None:
            plan = self.session.plan
            if plan is None:
                return
            states = plan["persistent_state"]["items"]
            state_lines = [
                f"• {item.get('id')} — {item.get('disposition')} — "
                f"{item.get('path') or 'destino pendiente'}"
                for item in states
            ]
            pending = plan["persistent_state"].get(
                "unclassified_candidates", []
            )
            if pending:
                state_lines.append("")
                state_lines.append("Detectados todavía sin clasificar:")
                state_lines.extend(f"  • {path}" for path in pending)
            self.state_list.set_text(
                "\n".join(state_lines) or "Sin estado declarado."
            )
            supplemental = plan.get("supplemental_content", [])
            self.supplemental_list.set_text(
                "\n".join(
                    f"• {item.get('id')} — {item.get('classification')}"
                    for item in supplemental
                ) or "Sin contenido declarado."
            )
            summary = {
                "capsule_id": plan["identity"].get("capsule_id"),
                "runner_binding": plan["runner"].get("binding"),
                "state_items": len(states),
                "unclassified": len(
                    plan["persistent_state"].get(
                        "unclassified_candidates", []
                    )
                ),
                "supplemental_content": len(supplemental),
                "profiles": [
                    item["id"] for item in plan["profiles"]
                    if item.get("enabled")
                ],
            }
            self.summary.set_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
            )
            self._update_action_sensitivity()

        def _scan_package(self, _button=None) -> None:
            self._sync_paths()
            source = self.session.source_package
            if source is None:
                self.status.set_text("Seleccione el paquete histórico.")
                return
            self._run_background(
                "Escaneo",
                lambda: self.session.scan_legacy(
                    source, vault=self.session.vault, full_hash=False
                ),
            )

        def _prepare_legacy(self, _button=None) -> None:
            self._apply_identity()
            self._apply_runner()
            self._sync_paths()
            if self.session.workspace is None:
                self.status.set_text("Indique un workspace nuevo.")
                return
            self._run_background(
                "Preparación de paquete",
                lambda: self.session.prepare_legacy(
                    self.session.workspace
                ),
            )

        def _prepare_manual(self, _button=None) -> None:
            self._apply_identity()
            self._apply_runner()
            self._sync_paths()
            game = self._entry("manual_game")
            prefix = self._entry("manual_prefix")
            if not game or self.session.workspace is None:
                self.status.set_text(
                    "Indique el directorio del juego y un workspace nuevo."
                )
                return
            self._run_background(
                "Preparación manual",
                lambda: self.session.prepare_manual(
                    game=Path(game),
                    prefix=Path(prefix) if prefix else None,
                    workspace=self.session.workspace,
                ),
            )

        def _validate(self, _button=None) -> None:
            try:
                self._apply_identity()
                self._apply_runner()
                self._apply_profiles()
                self._sync_paths()
                result = self.session.validate("commit")
                if (
                    self.session.workspace is not None
                    and self.session.workspace.is_dir()
                    and (
                        self.session.workspace / "PREPARE_RECEIPT.json"
                    ).is_file()
                ):
                    write_json(
                        self.session.workspace / "IMPORT_PLAN.json",
                        self.session.plan,
                    )
                elif self.session.plan_path is not None:
                    write_json(self.session.plan_path, self.session.plan)
                self._operation_finished("Validación", result)
            except Exception as exc:
                self.status.set_text(f"Validación: ERROR — {exc}")

        def _verify_workspace(self, _button=None) -> None:
            self._sync_paths()
            self._run_background(
                "Verificación de workspace",
                self.session.verify,
            )

        def _commit(self, *, dry_run: bool) -> None:
            self._sync_paths()
            self._apply_identity()
            self._apply_runner()
            self._apply_profiles()
            label = "Ensayo de importación" if dry_run else "Importación"
            self._run_background(
                label,
                lambda: self.session.commit(dry_run=dry_run),
            )


    class ImporterApplication(Gtk.Application):
        def __init__(self):
            super().__init__(
                application_id="io.github.pbano.OfflineGameVault.Importer",
                flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            )

        def do_activate(self) -> None:
            window = self.props.active_window
            if window is None:
                window = ImporterWindow(self)
            window.present()


def main(argv: list[str] | None = None) -> int:
    _require_gtk()
    application = ImporterApplication()
    return int(application.run(argv or []))
