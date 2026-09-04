# Arquitectura — OfflineGameVault Importer 0.5.0a2

## Responsabilidad

El importer es la frontera entre una copia de juego ya preparada, legítima y
aislada de clientes de tienda y el Vault. No elimina integraciones de tienda,
modifica DRM, sustituye binarios ni descarga componentes.

```text
directorio jugable preparado
        │
        ▼
inspección orientativa y confirmación
        │
        ▼
ogv-import-plan-v4
        │
        ▼
workspace neutral autocontenido
        │
        ├── objeto de juego
        ├── estado privado opcional
        ├── contenido adicional opcional
        └── documentación
        │
        ▼
verify + dry-run + commit
        │
        ▼
OfflineGameVault Core 0.19.7+
        │
        ▼
cápsula game-source neutral
```

La GUI expone el flujo completo mediante **Preparar, verificar e importar
automáticamente** y conserva las operaciones separadas para diagnóstico.

## Límite de autoridad

El usuario es autoridad sobre la carpeta elegida, la identidad, el ejecutable
cuando la inspección es ambigua y los adjuntos seleccionados. La detección
automática solo propone valores.

El Core oficial es autoridad sobre:

- auditoría de la cápsula;
- ingestión y verificación del CAS;
- inventario;
- estado persistente verificable;
- listado de contenido opcional;
- composición posterior.

Antes de publicar, el importer exige Core 0.19.7 o posterior y comprueba los
comandos públicos que necesita. Una incompatibilidad detiene la operación antes
de modificar el Vault.

## Contrato de entrada

```text
contract: ogv-import-plan-v4
source.type: prepared-offline-game-directory-v1
source.store_client_independent: true
source.legitimate_source: true
source.third_party_drm: declared-absent
source.preparation.performed_before_import: true
```

`steam_independent` permanece en v4 como campo de compatibilidad. El alcance es
neutral respecto de Steam, Epic, GOG y cualquier otra tienda.

## Fuente neutral

El objeto inmutable contiene:

```text
payload/game/
payload/prefix-template/
NEUTRAL_LAYOUT.json
INVENTORY.json
INVENTORY_SEAL.json
```

La carpeta del juego, no `drive_c`, es la fuente principal. El prefix opcional
solo aporta contexto estructural avanzado. Un runner opcional se puede preservar
como objeto global reutilizable, pero nunca se añade como dependencia de la
fuente del juego.

La cápsula resultante publica exactamente:

```text
profile.id: game-source
profile.adapter: other
host contract: ogv-game-source-v1
runner binding: select-at-materialization
```

Bottles, Direct-Wine y UMU/Proton se eligen después en la GUI oficial. El Core
selecciona únicamente combinaciones técnicamente compatibles entre la fuente y
los componentes preservados.

## Contenido adicional

Bandas sonoras, artbooks, manuales, fondos, vídeos y otros extras se archivan
como objetos CAS independientes. Cada elemento tiene un ID, clasificación y una
colocación explícita:

- `sidecar`: publicación aislada bajo `extras/`;
- `game-overlay`: superposición declarada dentro del árbol lógico del juego.

La GUI oficial obtiene estos elementos mediante `list-optional-content` y pasa
solo los IDs seleccionados a `compose --content-id`.

## Estado y privacidad

El estado privado se mantiene fuera del objeto inmutable. Los save sets pueden
agrupar varios elementos y solo son restaurables cuando tienen destinos
relativos explícitos.

El workspace conserva dos planes:

```text
IMPORT_PLAN.json         privado y operativo
PUBLIC_IMPORT_PLAN.json  publicable y sin rutas del anfitrión
```

El commit publica únicamente el plan saneado. Las rutas fuente, el checkout y
el comando local del Core no entran en la cápsula.

## Aceptación

La importación acredita estructura, integridad y compatibilidad de contrato. No
acredita arranque, partidas, DLC, audio, vídeo, mando, aislamiento de red,
cierre normal ni restauración. Esa evidencia pertenece a cada materialización
posterior.
