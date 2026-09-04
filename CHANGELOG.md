# Changelog

## 0.5.0a1 — 2026-09-04

- Require Offline Game Vault Core 0.19.7 or newer and probe the complete public
  CLI contract before any Vault publication.
- Add `ogv-import check-core` and a live cross-repository compatibility test
  that imports, discovers and materializes a game plus optional content.
- Make `game-source` with `adapter=other` the only profile emitted by the
  importer; backend and runner selection are exclusively materialization-time
  decisions.
- Remove the retired `profile.status` field so imported capsules are accepted
  by the current Offline Game Vault GUI catalog.
- Add the GUI action **Preparar, verificar e importar automáticamente**, which
  runs prepare, workspace verification, dry-run and transactional commit.
- Generalize the prepared source declaration from Steam-only wording to any
  store-independent folder while retaining the v4 compatibility field.
- Record the probed Core version, source and required command set in the import
  receipt.
- Replace the obsolete backend-profile editor with an explicit neutral-source
  explanation and keep optional runners unbound.

## 0.4.2 — 2026-08-21

- Publish present `supplemental_content` as first-class immutable objects and
  emit the Core 0.19.7 `optional_content` capsule contract.
- Add explicit `game-overlay` and `sidecar` placement metadata. Existing v4
  plans without placement default conservatively to `sidecar/<content-id>`.
- Optional objects are ingested through Core with their declared archive
  format, so canonical manifests and sidecars are generated on ingest.
- Supplemental content is no longer copied into
  `02_CAPSULES/<id>/supplemental-content` by the normal commit path.
- Symlinks and special files are rejected before optional content is packaged,
  matching the Core materialization safety contract.


## 0.4.1 — 2026-08-21

- Pass the declared archive format to Core `ingest-object` for newly
  imported game and runner objects.
- This makes Core generate canonical per-object manifests during ingest,
  so a freshly imported capsule can satisfy composition manifest preflight.
- No Vault migration, backend compatibility change, or GUI change is made.


## 0.4.0 — 2026-08-21

- New imports use `ogv-import-plan-v4`.
- New prepared games publish one canonical backend-neutral `game-source`
  profile backed by `ogv-game-source-v1`.
- Backend and runner selection move out of the importer and remain
  materialization-time user choices.
- A preserved runner is still reusable globally but is not bound as a
  dependency of the canonical game source.
- `source.game_directory` is explicitly the directory containing the game
  binaries; a complete `drive_c` is not imported as the game by default.


## 0.3.1 — 2026-08-01

- Corregido el ensayo y commit de cápsulas sin estado persistente declarado.
- El importer ya no invoca `preserve-state` ni fabrica un backup vacío cuando
  no hay partidas, identidad o configuración seleccionadas.
- El recibo registra `backup_status: not-applicable` y valores nulos para el
  backup aceptado.
- Añadida una prueba de regresión que reproduce el rechazo del núcleo.

## 0.3.0 — 2026-08-01

- Sustituido el flujo principal de migración histórica por un único modelo:
  `prepared-offline-game-directory-v1`.
- Eliminados de CLI y GUI los caminos públicos de paquete histórico.
- Añadida inspección orientativa de ejecutables y DLLs Steamworks.
- Añadidas sugerencias editables para `capsule_id`, `profile_id`, save-set y
  contenido adicional.
- Añadido soporte gráfico y contractual para UMU.
- Añadida selección manual de documentación con roles y nombres canónicos.
- Partidas, contenido adicional, documentación y runner se copian al workspace
  durante `prepare`.
- Añadido `PUBLIC_IMPORT_PLAN.json` sin rutas privadas.
- El commit publica documentos seleccionados sin reescribirlos y genera
  plantillas solo para roles raíz ausentes.
- Actualizado el esquema a `ogv-import-plan-v3`.
- Añadidas cuatro pruebas del nuevo flujo; total: 25.
- La importación sigue publicando candidatos, nunca perfiles verificados.

## 0.2.1

- Corrige la resolución de estados incluidos dentro del Full Archive cuando la GUI conserva un `source_path` relativo o obsoleto.
- Usa la ruta declarada dentro del prefix como fallback seguro y registra `source_resolution`.
- Recupera workspaces vacíos que la GUI 0.2.0 dejó únicamente con `IMPORT_PLAN.json`.
- Mantiene deshabilitados Verificar, Ensayo e Importar hasta que exista un workspace preparado.
- Validar selección ya no crea por sí solo un workspace no preparado.

## 0.2.0 — 2026-07-24

- Añade GUI GTK4 propia.
- Añade planes manuales y selectores explícitos.
- La detección automática deja de ser autoridad.
- Permite importar candidatos con runner sin vincular.
- Permite seleccionar runner como archivo o directorio.
- Deduplica runners por SHA-256.
- Añade save-sets multielemento y payloads de fichero o directorio.
- Permite estado incompleto o sin destino como pendiente declarado.
- Añade contenido suplementario manual.
- Añade perfiles candidatos Bottles, Direct-Wine y Windows.
- Añade `commit-vault` con staging, bloqueo, ingestión, recibos y rollback.
- Publica cápsula, estado, save-sets, índices e inventario.
- Conserva metadatos Bottles brutos en workspace privado y publica una
  plantilla saneada.
- Mantiene `--dry-run` como precondición recomendada.

## 0.1.2 — 2026-07-24

- Saneamiento automático de procedencia MSI y fuentes externas.
- Informes de privacidad por coincidencia.
- Workspace neutral verificable sin escritura en el Vault.
