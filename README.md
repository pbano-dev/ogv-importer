# OfflineGameVault Importer 0.2.1

Asistente gráfico y CLI para incorporar juegos de Windows al Vault como
**candidatos materializables**.

La detección automática es orientativa. La selección efectiva del usuario es
la autoridad: juego, prefix, ejecutable, runner, partidas, configuración y
contenido adicional pueden señalarse manualmente.

## Modelo

```text
fuente histórica o selección manual
        ↓
IMPORT_PLAN.json
        ↓
workspace neutral verificado
        ↓
ensayo transaccional
        ↓
Importar al Vault
```

El importador separa:

```text
payload/game
payload/prefix-template
estado persistente
metadatos Bottles
runner/runtime compartido
contenido suplementario
```

El Full Archive de Bottles es una fuente de importación, no el formato
canónico universal.

## Qué publica

Una importación correcta crea o actualiza:

```text
01_IMMUTABLE_VAULT/
  objects/sha256/...                objeto neutral del juego
  objects/sha256/...                runner nuevo, solo si se seleccionó y no existe

02_CAPSULES/<capsule_id>/
  capsule.json
  PROVENANCE.json
  CONTENT_STATUS.json
  docs/
  evidence/
  host-contracts/
  supplemental-content/

03_PERSISTENT_STATE/<capsule_id>/
  accepted/
  save-sets/
  snapshots/
  history/

04_RECEIPTS/<capsule_id>/operations/<operation_id>/
05_PRIVATE_WORKSPACES/<capsule_id>/
06_DERIVED_MATERIALIZATIONS/<capsule_id>/
07_EXPORTS/<capsule_id>/
```

También actualiza `INDEX.json`, `COLLECTION_LAYOUT.json`,
`COLLECTION_SHA256.txt` y `VAULT_INVENTORY.json`.

## Candidatos, no garantías

La ausencia de runner, partidas, DLC o una clasificación completa no impide
preservar el juego. Se registra la incertidumbre:

```text
candidate
not_tested
select-at-materialization
embedded-or-unknown
destination-unbound
```

`candidate` significa que la GUI principal puede intentar materializar el
perfil. No significa que el juego haya sido probado.

Solo bloquean el commit problemas estructurales o transaccionales: fuente
ilegible, hash incoherente, traversal, symlinks que escapan, archivos
especiales, colisión de capsule ID, Vault cambiado, falta de espacio o fallo
de rollback.

## Partidas multielemento

Una partida lógica puede contener varios elementos:

```text
Partida principal
├── save.dat
├── save.dat.bak
└── directorio de slots
```

Todos los elementos con el mismo `save_set_id` se publican juntos. La GUI
principal los restaura de forma atómica. Si el baseline no está demostrado
como limpio, se etiqueta `embedded-or-unknown`; no se presenta como
“Sin partida”.

## Runner

El runner puede:

- reutilizarse por SHA-256 desde el Vault;
- seleccionarse como archivo;
- seleccionarse como directorio, que se convierte a `tar.gz` determinista;
- dejarse sin vincular para elegirlo al materializar.

Un runner nuevo se publica como objeto `shared-runner`. Un digest ya conocido
no se duplica.

## Privacidad

Las rutas conocidas del anfitrión se sanean en el prefix neutral. Los
metadatos Bottles originales se conservan únicamente en
`05_PRIVATE_WORKSPACES`; la cápsula recibe, cuando existe, una plantilla
`bottle.yml` saneada.

Las referencias desconocidas continúan visibles en el informe. La política
`allow_privacy_pending` determina si la importación privada puede continuar
con pendientes declarados.

## GUI

Requisitos de escritorio: Python 3.11+, GTK 4 y PyGObject.

```bash
./scripts/run-gui.sh
```

o:

```bash
./scripts/ogv-import.sh gui
```

El asistente permite:

1. seleccionar el Vault y el origen;
2. escanear un paquete histórico o crear un plan manual;
3. elegir juego, prefix y ejecutable;
4. elegir o dejar sin vincular el runner;
5. añadir archivos o directorios de estado;
6. agrupar varios elementos en un save-set;
7. añadir contenido suplementario;
8. habilitar perfiles Bottles, Direct-Wine y Windows;
9. validar, ensayar y ejecutar el commit.

## CLI

```bash
./scripts/ogv-import.sh --help
```

### Paquete histórico

```bash
ogv-import scan-package \
  --source <PAQUETE> \
  --vault <VAULT> \
  --output import-scan.json

ogv-import init-plan \
  --scan import-scan.json \
  --output IMPORT_PLAN.json

ogv-import prepare \
  --plan IMPORT_PLAN.json \
  --source <PAQUETE> \
  --workspace <WORKSPACE_NUEVO>
```

### Selección manual

```bash
ogv-import new-manual-plan --output IMPORT_PLAN.json

ogv-import prepare-manual \
  --plan IMPORT_PLAN.json \
  --game <DIRECTORIO_DEL_JUEGO> \
  --prefix <PREFIX_OPCIONAL> \
  --workspace <WORKSPACE_NUEVO>
```

Los archivos o directorios de partidas, runner y contenido adicional se
declaran en el plan o desde la GUI.

### Verificar y publicar

```bash
ogv-import verify-workspace --workspace <WORKSPACE>

ogv-import commit-vault \
  --workspace <WORKSPACE> \
  --vault <VAULT> \
  --dry-run

ogv-import commit-vault \
  --workspace <WORKSPACE> \
  --vault <VAULT>
```

El commit usa el núcleo oficial `ogv` para auditar la cápsula, preservar y
verificar `accepted`, ingerir objetos e inventariar el almacén. Puede
localizarse mediante `OGV_SOURCE_ROOT`, `core.source_root`, `core.command` o un
`ogv` instalado.

## Seguridad transaccional

Antes de publicar:

- verifica el workspace y el objeto neutral;
- comprueba los documentos críticos del Vault;
- crea staging fuera de las rutas finales;
- audita la cápsula con el núcleo;
- genera y verifica `accepted`;
- ingiere por SHA-256 sin sobrescribir;
- publica directorios completos;
- actualiza manifiestos;
- revierte documentos, directorios y objetos nuevos si falla.

Los objetos preexistentes nunca se eliminan durante un rollback.

## Pruebas incluidas

```bash
./scripts/test.sh
```

La suite cubre escaneo seguro, preparación, saneamiento, save-sets
multielemento, runner ausente, runner manual como directorio, dry-run, commit
sintético, recibos y rollback básico.

## Límites de 0.2.1

Verificado mediante fixtures sintéticos:

- core, CLI y GUI;
- commit candidato;
- objeto de juego y runner;
- save-sets multielemento;
- contratos Bottles, Direct-Wine y Windows.

Pendiente de aceptación en el Vault real:

- commit de Nightreign;
- materialización real con Bottles;
- materialización real con Direct-Wine;
- exportación y prueba en Windows;
- gameplay, vídeo, audio, mando, DLC, red y cierre.

No aplica Steamless, gbe_fork, EAC ni otras modificaciones. Importa la copia
que el usuario ya ha preparado y documenta lo que encuentra.
