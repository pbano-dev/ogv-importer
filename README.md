# OfflineGameVault Importer

Herramienta gráfica y CLI para incorporar a OfflineGameVault un **directorio de
juego de Windows ya preparado y desacoplado de Steam**.

## Alcance

El importer empieza cuando ya existe una copia jugable que el usuario ha
preparado previamente. Para juegos procedentes de Steam y sin DRM de terceros,
esa copia puede contener Goldberg/gbe_fork y, cuando SteamStub lo requiera, un
ejecutable desempaquetado con Steamless.

El importer:

- copia el directorio jugable sin reinterpretarlo;
- genera inventario y SHA-256;
- separa estado persistente, contenido adicional y documentación;
- puede preservar un runner/runtime y un prefix inicial;
- construye un workspace autocontenido;
- valida y publica transaccionalmente un candidato en el Vault.

El importer **no**:

- instala ni conserva el cliente Steam;
- importa instaladores de Steam;
- descarga depots;
- aplica Steamless;
- sustituye `steam_api.dll` o `steam_api64.dll`;
- configura Goldberg/gbe_fork;
- retira DRM de terceros;
- declara aceptación funcional por el mero hecho de importar.

## Contrato único

La versión 0.3.1 utiliza un único modelo:

```text
ogv-import-plan-v4
source.type = prepared-offline-game-directory-v1
```

La CLI, la GUI, `prepare` y `commit-vault` rechazan planes v1/v2 y fuentes
históricas. El código auxiliar antiguo permanece únicamente para pruebas de
regresión y no constituye otro modo público de importación.

El plan confirma:

```text
steam_independent = true
legitimate_source = true
third_party_drm = declared-absent
preparation.performed_before_import = true
```

Estas declaraciones describen la entrada seleccionada. No sustituyen una
prueba posterior de arranque, partida, DLC, red o restauración.

## Flujo gráfico

```bash
ogv-import-gui
```

1. Seleccionar el Vault.
2. Seleccionar un workspace nuevo.
3. Seleccionar el directorio del juego desacoplado.
4. Pulsar **Inspeccionar y proponer**.
5. Revisar título, `capsule_id`, ejecutable y destino.
6. Añadir opcionalmente:
   - prefix;
   - runner/runtime;
   - partidas e identidad;
   - contenido adicional;
   - documentación.
7. Elegir perfiles candidatos.
8. Preparar el workspace.
9. Verificar.
10. Ejecutar un dry-run.
11. Importar al Vault.

La inspección propone ejecutables e identificadores. La selección del usuario
sigue siendo la autoridad.

## Nomenclatura sugerida

A partir del título o del nombre del directorio se generan ejemplos editables:

```text
Título:        ELDEN RING NIGHTREIGN
capsule_id:   elden-ring-nightreign

profile_id:
linux-bottles-flatpak
linux-direct-wine
linux-umu-proton
windows-native

save_set_id:
elden-ring-nightreign-main

state_id:
elden-ring-nightreign-save
```

Los identificadores usan minúsculas ASCII y separadores `-`, `_` o `.`. Una
sugerencia no es evidencia y puede cambiarse antes del commit.

## CLI

### Inspeccionar un directorio

```bash
ogv-import inspect-game \
  --game "$HOME/Juegos/<JUEGO>" \
  --title "<JUEGO>" \
  --output inspection.json
```

La inspección muestra:

- ejecutables candidatos;
- DLLs `steam_api*.dll` y sus hashes;
- número de archivos y tamaño;
- symlinks;
- nomenclatura sugerida.

No demuestra que el juego arranque ni ausencia de DRM de terceros.

### Crear un plan

```bash
ogv-import new-plan \
  --game "$HOME/Juegos/<JUEGO>" \
  --title "<JUEGO>" \
  --output IMPORT_PLAN.json
```

Revise como mínimo:

```text
identity.preserved_version
identity.capsule_id
layout.entrypoint
layout.game_destination_in_prefix
layout.working_directory
profiles
```

### Preparar el workspace

```bash
ogv-import prepare \
  --plan IMPORT_PLAN.json \
  --game "$HOME/Juegos/<JUEGO>" \
  --workspace "$HOME/OGV-workspaces/<JUEGO>"
```

Con prefix opcional:

```bash
ogv-import prepare \
  --plan IMPORT_PLAN.json \
  --game "$HOME/Juegos/<JUEGO>" \
  --prefix "$HOME/Prefixes/<JUEGO>" \
  --workspace "$HOME/OGV-workspaces/<JUEGO>"
```

### Verificar y publicar

```bash
ogv-import verify-workspace \
  --workspace "$HOME/OGV-workspaces/<JUEGO>"

ogv-import commit-vault \
  --workspace "$HOME/OGV-workspaces/<JUEGO>" \
  --vault "$HOME/OfflineGameVault" \
  --dry-run

ogv-import commit-vault \
  --workspace "$HOME/OGV-workspaces/<JUEGO>" \
  --vault "$HOME/OfflineGameVault"
```

## Partidas y estado

Cada elemento puede ser un archivo o directorio seleccionado manualmente.

Disposiciones:

```text
save-set
identity
configuration
exclude
embedded
unbound
```

Varios elementos con el mismo `save_set_id` forman una unidad lógica. La ruta
de restauración es relativa al root de estado del perfil; no se adivina.

Una partida sin destino conocido puede preservarse con `disposition=unbound`,
pero no se declara restaurable.

## Fuente de juego neutral v4

La entrada principal es la **carpeta del juego que contiene sus binarios**,
no la raíz `drive_c` de una Bottle o Wine prefix. `drive_c` describe la
topología donde el Core volverá a colocar el juego al materializar.

Un prefix de origen puede servir más adelante como contexto para detectar
material estructural externo al directorio del juego, pero no se preserva
completo por defecto. Backend y runner se eligen durante la materialización,
no durante la importación.

## Contenido adicional

Desde 0.4.2, el contenido adicional presente se publica como objetos
inmutables seleccionables por Core, no como copias locales dentro de la
cápsula. Cada elemento declara una colocación explícita:

- `sidecar`: se materializa aislado bajo `extras/`; es el valor conservador
  por defecto para planes v4 antiguos sin `placement`.
- `game-overlay`: sólo se usa cuando el plan lo declara explícitamente,
  con un destino relativo dentro del árbol lógico del juego.

El importer no infiere que un manual, artbook, soundtrack o cualquier otro
adjunto deba superponerse sobre los archivos del juego.



` supplemental_content ` admite archivos o directorios como:

```text
artbook
soundtrack
manual
wallpapers
bonus-video
edition-bonus
other
```

El contenido se copia al workspace durante `prepare`. El commit ya no depende
de que la ruta original siga existiendo.

## Documentación

`documentation` permite seleccionar documentos existentes y asignarles un rol:

```text
readme
game_sheet
credits
preserved_by
technical_notes
addendum
other
```

Nombres canónicos habituales:

```text
00_README.md
FICHA_DEL_JUEGO.md
CREDITOS.md
PRESERVADO_POR.md
NOTAS_TECNICAS_DEL_PROCESO.md
ADDENDUM_<JUEGO>.md
```

El archivo seleccionado se conserva byte a byte bajo el nombre canónico
indicado. Si falta uno de los cuatro documentos raíz obligatorios, se genera
una plantilla marcada con `[RELLENAR]`, `[VERIFICAR]` o `[NO PROBADO]`.

## Workspace

```text
<WORKSPACE>/
├── IMPORT_PLAN.json
├── PUBLIC_IMPORT_PLAN.json
├── PREPARE_RECEIPT.json
├── neutral-object/
│   ├── payload/game/
│   ├── payload/prefix-template/
│   ├── NEUTRAL_LAYOUT.json
│   ├── INVENTORY.json
│   └── INVENTORY_SEAL.json
├── selected-components/
│   ├── runner/
│   ├── supplemental_content/
│   └── documentation/
├── private-state/
├── objects/neutral-game.tar.gz
├── reports/
└── draft/CAPSULE_INPUT.json
```

`IMPORT_PLAN.json` conserva rutas locales mientras el workspace existe.
`PUBLIC_IMPORT_PLAN.json` elimina rutas fuente, checkout del core y comandos
locales. La cápsula publica únicamente la copia saneada.

## Perfiles

Perfiles disponibles:

```text
Bottles
Direct-Wine
UMU/Proton
Windows nativo
```

Bottles se habilita por defecto. Los demás se habilitan manualmente cuando
aplican. El importer solo publica estados `candidate`, `experimental`,
`not_tested` o `unavailable`; rechaza `verified`.

## Integridad y privacidad

La preparación:

- rechaza archivos especiales;
- rechaza symlinks en partidas, documentación y contenido adicional;
- conserva y audita los symlinks del juego;
- genera un objeto neutral determinista;
- sella `INVENTORY.json`;
- genera SHA-256;
- separa el plan privado del plan publicable;
- conserva las selecciones manuales dentro del workspace.

Los logs brutos no deben añadirse como documentación por defecto porque pueden
contener rutas absolutas y datos del anfitrión.

## Estado de aceptación

Una importación correcta verifica estructura e integridad. Siguen pendientes,
salvo evidencia posterior:

```text
arranque sin Steam
partida existente
DLC real
vídeo y audio
mando y hotplug
aislamiento de red
cierre normal
reubicación
restauración limpia
Windows nativo
```

## Pruebas

```bash
bash scripts/test.sh
```

La suite 0.3.1 cubre el flujo preparado, nomenclatura, inspección, partidas
multielemento, ausencia válida de estado persistente, documentación seleccionada,
contenido adicional, privacidad, dry-run, commit y rollback, además de pruebas
de seguridad de archivo heredadas
que siguen protegiendo las primitivas internas.
