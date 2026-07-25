# Changelog

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
