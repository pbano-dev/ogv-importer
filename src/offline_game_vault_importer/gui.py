from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

from .errors import ImporterError
from .gui_guidance import (
    CHOICE_GUIDANCE,
    FIELD_GUIDANCE,
    PENDING_COMPONENT_FIELDS,
    pending_component_sections,
)
from .gui_model import ImportSession
from . import __version__
from .planner import new_prepared_plan
from .naming import suggest_identifiers

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
                title=f"OfflineGameVault Importer {__version__}",
            )
            self.set_default_size(1040, 760)
            self.session = ImportSession()
            self.entries: dict[str, Gtk.Entry] = {}
            self.example_labels: dict[str, Gtk.Label] = {}
            self._busy = False
            self._pulse_source_id: int | None = None
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
            self.new_button = Gtk.Button(label="Nuevo plan")
            self.new_button.connect("clicked", self._new_manual)
            header.append(self.new_button)
            self.load_button = Gtk.Button(label="Cargar plan…")
            self.load_button.connect("clicked", self._load_plan_dialog)
            header.append(self.load_button)
            root.append(header)

            self.notebook = Gtk.Notebook()
            self.notebook.set_vexpand(True)
            root.append(self.notebook)

            self._build_paths_page()
            self._build_identity_page()
            self._build_components_page()
            self._build_state_page()
            # Backend + runner are materialization-time choices in the main GUI.
            self.profile_checks = {}
            self.profile_entries = {}
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
            self.progress.set_show_text(True)
            self.progress.set_pulse_step(0.025)
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
            guidance = FIELD_GUIDANCE[key]
            container = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=3,
            )
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            text.set_size_request(180, -1)
            row.append(text)
            row.append(self._help_button(key, label, guidance))
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_placeholder_text(guidance["example"])
            entry.set_tooltip_text(guidance["help"])
            self.entries[key] = entry
            row.append(entry)
            if folder is None:
                file_button = Gtk.Button(label="Archivo…")
                file_button.connect(
                    "clicked",
                    lambda _button: self._choose_path(
                        entry, folder=False, key=key
                    ),
                )
                row.append(file_button)
                folder_button = Gtk.Button(label="Directorio…")
                folder_button.connect(
                    "clicked",
                    lambda _button: self._choose_path(
                        entry, folder=True, key=key
                    ),
                )
                row.append(folder_button)
            else:
                button = Gtk.Button(
                    label=(
                        "Elegir carpeta padre…"
                        if key == "workspace"
                        else "Seleccionar…"
                    )
                )
                button.connect(
                    "clicked",
                    lambda _button: self._choose_path(
                        entry, folder=folder, key=key
                    ),
                )
                row.append(button)
            container.append(row)
            container.append(self._example_label(key, guidance["example"]))
            box.append(container)

        def _field(
            self,
            grid: Gtk.Grid,
            row: int,
            key: str,
            label: str,
        ) -> None:
            guidance = FIELD_GUIDANCE[key]
            label_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=4,
            )
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            label_box.append(text)
            label_box.append(self._help_button(key, label, guidance))
            grid.attach(label_box, 0, row, 1, 1)
            value_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=3,
            )
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_placeholder_text(guidance["example"])
            entry.set_tooltip_text(guidance["help"])
            self.entries[key] = entry
            value_box.append(entry)
            value_box.append(self._example_label(key, guidance["example"]))
            grid.attach(value_box, 1, row, 1, 1)

        def _help_button(
            self,
            key: str,
            label: str,
            guidance: dict[str, str],
        ) -> Gtk.Button:
            button = Gtk.Button(label="?")
            button.add_css_class("flat")
            button.set_tooltip_text(guidance["help"])
            button.connect(
                "clicked",
                lambda _button: self._show_field_help(key, label),
            )
            return button

        def _example_label(self, key: str, example: str) -> Gtk.Label:
            value = Gtk.Label(label=f"Ejemplo: {example}")
            value.set_xalign(0)
            value.set_wrap(True)
            value.set_selectable(True)
            value.add_css_class("dim-label")
            self.example_labels[key] = value
            return value

        def _show_field_help(self, key: str, label: str) -> None:
            guidance = FIELD_GUIDANCE.get(key) or CHOICE_GUIDANCE[key]
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.CLOSE,
                text=label,
            )
            dialog.format_secondary_text(
                f"{guidance['help']}\n\nEjemplo: {guidance['example']}"
            )
            dialog.connect("response", lambda current, _response: current.destroy())
            dialog.present()

        def _choice_row(
            self,
            box: Gtk.Box,
            key: str,
            label: str,
            options: list[str],
        ) -> Gtk.DropDown:
            guidance = CHOICE_GUIDANCE[key]
            container = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=3,
            )
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            text = Gtk.Label(label=label)
            text.set_xalign(0)
            text.set_size_request(180, -1)
            row.append(text)
            row.append(self._help_button(key, label, guidance))
            dropdown = Gtk.DropDown.new_from_strings(options)
            dropdown.set_hexpand(True)
            dropdown.set_tooltip_text(guidance["help"])
            row.append(dropdown)
            container.append(row)
            container.append(self._example_label(key, guidance["example"]))
            box.append(container)
            return dropdown

        def _build_paths_page(self) -> None:
            _, box = self._page("1 · Origen")
            note = Gtk.Label(
                label=(
                    "Seleccione un directorio de juego ya funcional y aislado "
                    "de cualquier tienda. Partidas, contenido adicional y "
                    "documentación son opcionales."
                )
            )
            note.set_wrap(True)
            note.set_xalign(0)
            box.append(note)
            self._path_row(box, "vault", "Vault de destino", folder=True)
            self._path_row(box, "workspace", "Workspace nuevo", folder=True)
            self._path_row(
                box, "manual_game", "Directorio del juego desacoplado", folder=True
            )
            self._path_row(box, "manual_prefix", "Prefix inicial (opcional)", folder=True)
            self._path_row(box, "core_root", "Checkout del núcleo", folder=True)

            core_check = Gtk.Button(label="Comprobar Core 0.19.7+")
            core_check.connect("clicked", self._check_core)
            box.append(core_check)

            actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            inspect = Gtk.Button(label="Inspeccionar y proponer")
            inspect.connect("clicked", self._inspect_game)
            actions.append(inspect)
            prepare = Gtk.Button(label="Preparar importación")
            prepare.add_css_class("suggested-action")
            prepare.connect("clicked", self._prepare_manual)
            actions.append(prepare)
            box.append(actions)

        def _build_identity_page(self) -> None:
            _, box = self._page("2 · Identidad")
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

            actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            suggest = Gtk.Button(label="Proponer nomenclatura")
            suggest.connect("clicked", self._suggest_naming)
            actions.append(suggest)
            save = Gtk.Button(label="Aplicar identidad y layout")
            save.connect("clicked", self._apply_identity)
            actions.append(save)
            box.append(actions)

        def _build_components_page(self) -> None:
            _, box = self._page("3 · Componentes")
            runner_frame = Gtk.Frame(
                label="Runner adicional a preservar (opcional)"
            )
            runner_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            runner_box.set_margin_top(8)
            runner_box.set_margin_bottom(8)
            runner_box.set_margin_start(8)
            runner_box.set_margin_end(8)
            runner_frame.set_child(runner_box)
            box.append(runner_frame)

            self.runner_binding = self._choice_row(
                runner_box,
                "runner_binding",
                "Vinculación",
                ["Elegir al materializar"],
            )
            runner_note = Gtk.Label(
                label=(
                    "El runner se conserva como objeto reutilizable, pero no "
                    "queda vinculado al juego. La GUI oficial lo seleccionará "
                    "durante la materialización."
                )
            )
            runner_note.set_xalign(0)
            runner_note.set_wrap(True)
            runner_box.append(runner_note)
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
            self.entries["supplemental_class"].set_placeholder_text(
                "soundtrack, artbook, manual, wallpapers…"
            )
            add = Gtk.Button(label="Añadir contenido")
            add.connect("clicked", self._add_supplemental)
            extra_box.append(add)
            self.supplemental_list = Gtk.Label(label="Sin contenido declarado.")
            self.supplemental_list.set_xalign(0)
            self.supplemental_list.set_wrap(True)
            extra_box.append(self.supplemental_list)

            docs = Gtk.Frame(label="Documentación añadida")
            docs_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            docs_box.set_margin_top(8)
            docs_box.set_margin_bottom(8)
            docs_box.set_margin_start(8)
            docs_box.set_margin_end(8)
            docs.set_child(docs_box)
            box.append(docs)
            self._path_row(
                docs_box, "documentation_source", "Documento", folder=False
            )
            docs_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            docs_box.append(docs_grid)
            self._field(docs_grid, 0, "documentation_id", "ID")
            self._field(docs_grid, 1, "documentation_role", "Rol")
            self._field(
                docs_grid, 2, "documentation_name", "Nombre canónico"
            )
            doc_add = Gtk.Button(label="Añadir documentación")
            doc_add.connect("clicked", self._add_documentation)
            docs_box.append(doc_add)
            self.documentation_list = Gtk.Label(
                label="Sin documentación seleccionada; se generarán plantillas."
            )
            self.documentation_list.set_xalign(0)
            self.documentation_list.set_wrap(True)
            docs_box.append(self.documentation_list)

        def _build_state_page(self) -> None:
            _, box = self._page("4 · Partidas y estado")
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

            self.baseline_state = self._choice_row(
                box,
                "baseline_state",
                "Estado inicial",
                [
                    "No confirmado / posiblemente incrustado",
                    "Limpio / sin estado incrustado",
                ],
            )

            self._path_row(
                box, "state_source", "Fuente del estado", folder=None
            )
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            box.append(grid)
            self._field(grid, 0, "state_id", "ID del estado")
            self._field(grid, 1, "state_destination", "Destino relativo")
            self._field(grid, 2, "save_set_id", "Save-set")
            self._field(grid, 3, "save_display", "Nombre visible")

            self.state_disposition = self._choice_row(
                box,
                "state_disposition",
                "Cómo tratarlo",
                [
                    "Partida restaurable (save-set)",
                    "Identidad o cuenta",
                    "Configuración",
                    "Excluir",
                    "Ya incluido en el juego",
                    "Sin destino conocido",
                ],
            )

            add = Gtk.Button(label="Añadir estado")
            add.connect("clicked", self._add_state)
            box.append(add)
            self.state_list = Gtk.Label(label="Sin estado declarado.")
            self.state_list.set_xalign(0)
            self.state_list.set_wrap(True)
            self.state_list.set_selectable(True)
            box.append(self.state_list)

        def _build_profiles_page(self) -> None:
            _, box = self._page("Fuente neutral")
            self.profile_checks: dict[str, Gtk.CheckButton] = {}
            self.profile_entries: dict[str, Gtk.Entry] = {}
            note = Gtk.Label(
                label=(
                    "La importación publica únicamente el perfil neutral "
                    "game-source. Bottles, Direct-Wine y UMU/Proton no se "
                    "eligen aquí: la GUI oficial y el Core los compondrán más "
                    "tarde con el runner preservado que seleccione el usuario."
                )
            )
            note.set_xalign(0)
            note.set_wrap(True)
            box.append(note)

        def _build_commit_page(self) -> None:
            _, box = self._page("5 · Verificar e importar")
            note = Gtk.Label(
                label=(
                    "Verifique toda la configuración antes de preparar o "
                    "importar. El resultado muestra cada comprobación y una "
                    "acción concreta cuando algo requiere atención."
                )
            )
            note.set_xalign(0)
            note.set_wrap(True)
            box.append(note)

            result_frame = Gtk.Frame(label="Resultado")
            result_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=8,
            )
            result_box.set_margin_top(10)
            result_box.set_margin_bottom(10)
            result_box.set_margin_start(10)
            result_box.set_margin_end(10)
            result_frame.set_child(result_box)
            box.append(result_frame)
            self.result_banner = Gtk.Label(
                label="Aún no se ha ejecutado la verificación general."
            )
            self.result_banner.set_xalign(0)
            self.result_banner.set_wrap(True)
            result_box.append(self.result_banner)
            self.checklist = Gtk.Label(label="")
            self.checklist.set_xalign(0)
            self.checklist.set_wrap(True)
            self.checklist.set_selectable(True)
            result_box.append(self.checklist)

            technical = Gtk.Expander(label="Detalles técnicos")
            self.summary = Gtk.Label(
                label="Cargue o cree un plan para ver el resumen."
            )
            self.summary.set_xalign(0)
            self.summary.set_wrap(True)
            self.summary.set_selectable(True)
            technical.set_child(self.summary)
            box.append(technical)
            actions = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            self.validate_button = Gtk.Button(
                label="Verificar configuración completa"
            )
            self.validate_button.add_css_class("suggested-action")
            self.validate_button.connect("clicked", self._validate_all)
            actions.append(self.validate_button)
            advanced_actions = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            self.verify_button = Gtk.Button(label="Verificar workspace")
            self.verify_button.connect("clicked", self._verify_workspace)
            self.verify_button.set_sensitive(False)
            advanced_actions.append(self.verify_button)
            self.dry_button = Gtk.Button(label="Ensayo de importación")
            self.dry_button.connect(
                "clicked", lambda _b: self._commit(dry_run=True)
            )
            self.dry_button.set_sensitive(False)
            advanced_actions.append(self.dry_button)
            self.commit_button = Gtk.Button(label="Importar al Vault")
            self.commit_button.add_css_class("suggested-action")
            self.commit_button.connect(
                "clicked", lambda _b: self._commit(dry_run=False)
            )
            self.commit_button.set_sensitive(False)
            advanced_actions.append(self.commit_button)
            actions.append(advanced_actions)
            box.append(actions)
            self.automatic_button = Gtk.Button(
                label="Preparar, verificar e importar automáticamente"
            )
            self.automatic_button.add_css_class("suggested-action")
            self.automatic_button.connect("clicked", self._automatic_import)
            box.append(self.automatic_button)

        def _choose_path(
            self,
            entry: Gtk.Entry,
            *,
            folder: bool,
            key: str,
        ) -> None:
            dialog = Gtk.FileChooserNative(
                title=(
                    "Elegir dónde crear el workspace"
                    if key == "workspace"
                    else "Seleccionar"
                ),
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
                        selected_path = Path(selected.get_path())
                        if key == "workspace":
                            title = self._entry("title") or None
                            game = self._entry("manual_game") or None
                            suggestion = suggest_identifiers(
                                title=title,
                                game_directory=game,
                            )["capsule_id"]
                            candidate = selected_path / f"{suggestion}-import"
                            suffix = 2
                            while candidate.exists() or candidate.is_symlink():
                                candidate = selected_path / (
                                    f"{suggestion}-import-{suffix}"
                                )
                                suffix += 1
                            entry.set_text(str(candidate))
                            self.status.set_text(
                                "Se creará un workspace nuevo en "
                                f"{candidate}."
                            )
                        else:
                            entry.set_text(str(selected_path))
                native.destroy()
            dialog.connect("response", response)
            dialog.show()

        def _set_busy(self, busy: bool, message: str = "") -> None:
            self._busy = busy
            self.progress.set_visible(busy)
            if busy:
                self.progress.set_text(message or "Trabajando…")
                if self._pulse_source_id is None:
                    self._pulse_source_id = GLib.timeout_add(
                        100, self._pulse_progress
                    )
            elif self._pulse_source_id is not None:
                GLib.source_remove(self._pulse_source_id)
                self._pulse_source_id = None
                self.progress.set_fraction(0.0)
                self.progress.set_text("")
            self.new_button.set_sensitive(not busy)
            self.load_button.set_sensitive(not busy)
            self.notebook.set_sensitive(not busy)
            if message:
                self.status.set_text(message)
            if not busy:
                self._update_action_sensitivity()

        def _pulse_progress(self) -> bool:
            if not self._busy:
                self._pulse_source_id = None
                return False
            self.progress.pulse()
            return True

        def _operation_progress(
            self,
            stage: str,
            current: int,
            total: int,
        ) -> bool:
            message = f"Paso {current} de {total}: {stage}"
            self.progress.set_text(message)
            self.status.set_text(message)
            return False

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
            self.result_banner.set_text(f"✗ {label} no se pudo completar")
            self.checklist.set_text(
                f"• {detail}\n  Acción: revise los campos indicados y vuelva "
                "a ejecutar la verificación general."
            )
            if label in {"Preparación", "Importación automática"}:
                self.notebook.set_current_page(4)
            return False

        def _operation_finished(self, label: str, result: Any) -> bool:
            self._set_busy(False)
            rendered = json.dumps(
                result, ensure_ascii=False, indent=2, sort_keys=True
            )
            if label == "Inspección":
                # Load the automatically proposed AppID, layout and runner into
                # the editable fields. Detection remains advisory: the user can
                # replace every value before preparing the workspace.
                self._populate()
                self.notebook.set_current_page(1)
            else:
                self._refresh()
            self.summary.set_text(rendered)
            if isinstance(result, dict) and isinstance(
                result.get("checks"), list
            ):
                self._render_checks(label, result)
            else:
                status = (
                    result.get("status", "completado")
                    if isinstance(result, dict)
                    else "completado"
                )
                self.status.set_text(f"{label}: {status}")
                self.result_banner.set_text(f"✓ {label}: {status}")
                capsule_id = (
                    result.get("capsule_id")
                    if isinstance(result, dict)
                    else None
                )
                self.checklist.set_text(
                    f"Cápsula: {capsule_id}"
                    if capsule_id
                    else "La operación terminó correctamente."
                )
                if label in {"Preparación", "Importación automática"}:
                    self.notebook.set_current_page(4)
            return False

        def _render_checks(self, label: str, result: dict[str, Any]) -> None:
            status = result.get("status")
            errors = int(result.get("errors", 0))
            warnings = int(result.get("warnings", 0))
            if status == "valid":
                banner = (
                    f"✓ Configuración válida — {warnings} aviso(s)"
                    if warnings
                    else "✓ Configuración válida y lista"
                )
            else:
                banner = f"✗ Configuración incompleta — {errors} error(es)"
            lines: list[str] = []
            icons = {"ok": "✓", "warning": "⚠", "error": "✗"}
            for check in result["checks"]:
                icon = icons.get(check.get("status"), "•")
                lines.append(
                    f"{icon} {check.get('label')}: {check.get('detail')}"
                )
                action = check.get("action")
                if action:
                    lines.append(f"  Acción: {action}")
            self.result_banner.set_text(banner)
            self.checklist.set_text("\n".join(lines))
            self.status.set_text(f"{label}: {banner}")

        def _entry(self, key: str) -> str:
            return self.entries[key].get_text().strip()

        def _new_manual(self, _button: Gtk.Button) -> None:
            game = self._entry("manual_game") if "manual_game" in self.entries else ""
            title = self._entry("title") if "title" in self.entries else ""
            self.session.plan = new_prepared_plan(
                title=title or None,
                game_directory=game or None,
            )
            self.session.plan_path = None
            self.session.inspection = None
            self._populate()
            self.status.set_text("Plan nuevo para juego desacoplado.")
            self.result_banner.set_text(
                "Aún no se ha ejecutado la verificación general."
            )
            self.checklist.set_text("")

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
                "appid": (
                    ""
                    if identity.get("appid") is None
                    else str(identity.get("appid"))
                ),
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
                "manual_game": plan.get("source", {}).get("game_directory") or "",
                "manual_prefix": plan.get("source", {}).get("prefix_directory") or "",
            }
            for key, value in values.items():
                if key in self.entries:
                    self.entries[key].set_text(str(value))
            examples = plan.get("naming_examples", {})
            state_examples = examples.get("state_examples", {})
            if state_examples:
                self.entries["state_id"].set_placeholder_text(
                    state_examples.get("state_id", "")
                )
                self.entries["save_set_id"].set_placeholder_text(
                    state_examples.get("save_set_id", "")
                )
            supplemental_examples = examples.get(
                "supplemental_examples", {}
            )
            if supplemental_examples:
                self.entries["supplemental_id"].set_placeholder_text(
                    supplemental_examples.get("artbook", "")
                )
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
            for profile in plan.get("profiles", []):
                adapter = profile.get("adapter")
                if adapter in self.profile_checks:
                    self.profile_checks[adapter].set_active(
                        bool(profile.get("enabled"))
                    )
                    self.profile_entries[adapter].set_text(
                        str(profile.get("id") or "")
                    )
            self._refresh()

        def _sync_paths(self) -> None:
            vault = self._entry("vault")
            workspace = self._entry("workspace")
            game = self._entry("manual_game")
            prefix = self._entry("manual_prefix")
            self.session.vault = Path(vault) if vault else None
            self.session.workspace = Path(workspace) if workspace else None
            self.session.game_directory = Path(game) if game else None
            if self.session.plan is not None:
                source = self.session.plan.setdefault("source", {})
                source["game_directory"] = game or None
                source["prefix_directory"] = prefix or None
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

        def _sync_identity_layout(self) -> None:
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

        def _sync_runner(self) -> None:
            self.session.set_runner(
                binding="select-at-materialization",
                source_path=self._entry("runner_source") or None,
                preferred_id=self._entry("runner_id") or None,
                digest=self._entry("runner_sha256") or None,
            )

        def _sync_profiles(self) -> None:
            if self.session.plan is None:
                raise ImporterError("no hay un plan cargado")
            for adapter, check in self.profile_checks.items():
                entry = self.profile_entries[adapter]
                self.session.set_profile(
                    adapter=adapter,
                    profile_id=(
                        entry.get_text().strip()
                        or entry.get_placeholder_text()
                    ),
                    enabled=check.get_active(),
                )

        def _sync_form_to_session(self) -> None:
            if self.session.plan is None:
                raise ImporterError(
                    "inspeccione primero el directorio para crear el plan"
                )
            self._sync_identity_layout()
            self._sync_runner()
            self._sync_profiles()
            self._sync_paths()

        def _pending_component_inputs(self) -> list[str]:
            keys = {
                key
                for fields in PENDING_COMPONENT_FIELDS.values()
                for key in fields
            }
            return pending_component_sections(
                {key: self._entry(key) for key in keys}
            )

        def _validation_with_pending_inputs(
            self,
            pending: list[str],
        ) -> dict[str, Any]:
            result = self.session.validate_configuration()
            if not pending:
                return result
            result["checks"].append(
                {
                    "id": "pending-inputs",
                    "label": "Datos todavía sin añadir",
                    "status": "error",
                    "detail": ", ".join(pending),
                    "action": (
                        "Pulse Añadir en cada sección o vacíe esos campos; "
                        "el texto escrito por sí solo aún no forma parte del plan."
                    ),
                }
            )
            result["errors"] = int(result.get("errors", 0)) + 1
            result["status"] = "invalid"
            result["ready_for_prepare"] = False
            result["ready_for_commit"] = False
            return result

        def _apply_identity(self, _button=None) -> None:
            try:
                self._sync_identity_layout()
                self._sync_paths()
                self.status.set_text("Identidad y layout aplicados.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _apply_runner(self, _button=None) -> None:
            try:
                self._sync_runner()
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

        def _suggest_naming(self, _button=None) -> None:
            try:
                suggestions = suggest_identifiers(
                    title=self._entry("title") or None,
                    game_directory=self._entry("manual_game") or None,
                )
                current_capsule = self._entry("capsule_id")
                if current_capsule in {"", "[RELLENAR]", "game"}:
                    self.entries["capsule_id"].set_text(suggestions["capsule_id"])
                destination = suggestions["game_destination_in_prefix"]
                current_destination = self._entry("game_destination")
                if current_destination in {
                    "", "[RELLENAR]", "drive_c/Games/game"
                }:
                    self.entries["game_destination"].set_text(destination)
                    self.entries["working_directory"].set_text(destination)
                for adapter, value in suggestions["profiles"].items():
                    if adapter in self.profile_entries:
                        current = self.profile_entries[adapter].get_text().strip()
                        if not current:
                            self.profile_entries[adapter].set_text(value)
                self.entries["state_id"].set_placeholder_text(
                    suggestions["state_examples"]["state_id"]
                )
                self.entries["save_set_id"].set_placeholder_text(
                    suggestions["state_examples"]["save_set_id"]
                )
                self.status.set_text(
                    "Nomenclatura propuesta. Revísela antes de aplicar."
                )
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _add_documentation(self, _button=None) -> None:
            try:
                role = self._entry("documentation_role") or "other"
                canonical = self._entry("documentation_name")
                if not canonical:
                    canonical = {
                        "readme": "00_README.md",
                        "game_sheet": "FICHA_DEL_JUEGO.md",
                        "credits": "CREDITOS.md",
                        "preserved_by": "PRESERVADO_POR.md",
                        "technical_notes": "NOTAS_TECNICAS_DEL_PROCESO.md",
                    }.get(role, Path(self._entry("documentation_source")).name)
                self.session.add_documentation(
                    item_id=self._entry("documentation_id"),
                    source_path=self._entry("documentation_source") or None,
                    role=role,
                    canonical_name=canonical,
                )
                for key in (
                    "documentation_id",
                    "documentation_source",
                    "documentation_role",
                    "documentation_name",
                ):
                    self.entries[key].set_text("")
                self.status.set_text("Documentación añadida.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _apply_profiles(self, _button=None) -> None:
            if self.session.plan is None:
                return
            try:
                self._sync_profiles()
                self.status.set_text("Perfiles aplicados.")
                self._refresh()
            except Exception as exc:
                self.status.set_text(f"Error: {exc}")

        def _update_action_sensitivity(self) -> None:
            workspace = self.session.workspace
            prepared = bool(
                workspace is not None
                and workspace.is_dir()
                and (workspace / "PREPARE_RECEIPT.json").is_file()
                and (workspace / "IMPORT_PLAN.json").is_file()
                and (workspace / "objects/neutral-game.tar.gz").is_file()
            )
            enabled = not self._busy
            self.validate_button.set_sensitive(
                enabled and self.session.plan is not None
            )
            self.verify_button.set_sensitive(enabled and prepared)
            self.dry_button.set_sensitive(enabled and prepared)
            self.commit_button.set_sensitive(enabled and prepared)
            self.automatic_button.set_sensitive(
                enabled and self.session.plan is not None and not prepared
            )

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
            documentation = plan.get("documentation", [])
            self.documentation_list.set_text(
                "\n".join(
                    f"• {item.get('id')} — {item.get('role')} — "
                    f"{item.get('canonical_name')}"
                    for item in documentation
                ) or "Sin documentación seleccionada; se generarán plantillas."
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
                "documentation": len(documentation),
                "profiles": [
                    item["id"] for item in plan["profiles"]
                    if item.get("enabled")
                ],
            }
            self.summary.set_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
            )
            self._update_action_sensitivity()

        def _inspect_game(self, _button=None) -> None:
            game = self._entry("manual_game")
            if not game:
                self.status.set_text("Seleccione el directorio del juego desacoplado.")
                return
            title = self._entry("title") or None
            self._run_background(
                "Inspección",
                lambda: self.session.inspect_game(Path(game), title=title),
            )

        def _check_core(self, _button=None) -> None:
            if self.session.plan is None:
                self.session.plan = new_prepared_plan(
                    title=self._entry("title") or None,
                    game_directory=self._entry("manual_game") or None,
                )
                self._populate()
            self._sync_paths()
            self._run_background(
                "Contrato del Core",
                self.session.check_core,
            )

        def _prepare_manual(self, _button=None) -> None:
            if self.session.plan is None:
                self.status.set_text(
                    "Inspeccione primero el directorio para crear el plan."
                )
                return
            try:
                self._sync_form_to_session()
            except Exception as exc:
                self._operation_failed("Preparación", str(exc))
                return
            game = self._entry("manual_game")
            prefix = self._entry("manual_prefix")
            if not game or self.session.workspace is None:
                self.status.set_text(
                    "Indique el directorio del juego y un workspace nuevo."
                )
                return
            pending = self._pending_component_inputs()

            def prepare_checked() -> dict[str, Any]:
                preflight = self._validation_with_pending_inputs(pending)
                if preflight["status"] != "valid":
                    failures = "; ".join(
                        item["detail"]
                        for item in preflight["checks"]
                        if item["status"] == "error"
                    )
                    raise ImporterError(
                        "la verificación general encontró errores: " + failures
                    )
                prepared = self.session.prepare(
                    game=Path(game),
                    prefix=Path(prefix) if prefix else None,
                    workspace=self.session.workspace,
                )
                return {
                    "schema": 0,
                    "status": "prepared-and-verified",
                    "preflight": preflight,
                    "prepare": prepared,
                }

            self._run_background(
                "Preparación",
                prepare_checked,
            )

        def _validate_all(self, _button=None) -> None:
            try:
                self._sync_form_to_session()
            except Exception as exc:
                self._operation_failed("Verificación general", str(exc))
                return
            pending = self._pending_component_inputs()
            self._run_background(
                "Verificación general",
                lambda: self._validation_with_pending_inputs(pending),
            )

        def _verify_workspace(self, _button=None) -> None:
            self._sync_paths()
            self._run_background(
                "Verificación de workspace",
                self.session.verify,
            )

        def _commit(self, *, dry_run: bool) -> None:
            label = "Ensayo de importación" if dry_run else "Importación"
            try:
                self._sync_form_to_session()
            except Exception as exc:
                self._operation_failed(label, str(exc))
                return
            pending = self._pending_component_inputs()

            def commit_checked() -> dict[str, Any]:
                preflight = self._validation_with_pending_inputs(pending)
                if preflight["status"] != "valid":
                    failures = "; ".join(
                        item["detail"]
                        for item in preflight["checks"]
                        if item["status"] == "error"
                    )
                    raise ImporterError(
                        "la verificación general encontró errores: " + failures
                    )
                committed = self.session.commit(dry_run=dry_run)
                return {**committed, "preflight": preflight}

            self._run_background(
                label,
                commit_checked,
            )

        def _automatic_import(self, _button=None) -> None:
            if self.session.plan is None:
                self.status.set_text(
                    "Inspeccione primero el directorio para crear el plan."
                )
                return
            try:
                self._sync_form_to_session()
            except Exception as exc:
                self._operation_failed("Importación automática", str(exc))
                return
            game = self._entry("manual_game")
            prefix = self._entry("manual_prefix")
            workspace = self.session.workspace
            pending = self._pending_component_inputs()
            if pending:
                self._operation_failed(
                    "Importación automática",
                    (
                        "hay datos escritos pero aún no añadidos: "
                        + ", ".join(pending)
                        + ". Pulse Añadir en cada sección o vacíe esos campos."
                    ),
                )
                return
            if not game or workspace is None or self.session.vault is None:
                self.status.set_text(
                    "Indique juego, workspace nuevo y Vault de destino."
                )
                return
            self._run_background(
                "Importación automática",
                lambda: self.session.import_prepared_game(
                    game=Path(game),
                    prefix=Path(prefix) if prefix else None,
                    workspace=workspace,
                    progress=lambda stage, current, total: GLib.idle_add(
                        self._operation_progress,
                        stage,
                        current,
                        total,
                    ),
                ),
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
