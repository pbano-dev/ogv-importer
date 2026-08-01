# Arquitectura — OfflineGameVault Importer 0.3.1

## Responsabilidad

El importer es la frontera entre una copia jugable ya desacoplada y el Vault.

```text
directorio jugable preparado
        │
        ▼
inspección orientativa
        │
        ▼
ogv-import-plan-v3
        │
        ▼
workspace neutral autocontenido
        │
        ├── juego/prefix
        ├── estado privado
        ├── runner
        ├── contenido adicional
        └── documentación
        │
        ▼
verify + dry-run
        │
        ▼
commit transaccional
        │
        ▼
OfflineGameVault
```

No existe un segundo modo de importación para instalaciones dependientes de
Steam ni para paquetes históricos.

## Autoridad

1. selección efectiva del usuario;
2. árbol copiado al workspace;
3. inventarios y hashes;
4. detección automática;
5. documentación descriptiva.

La inspección nunca reemplaza una selección confirmada.

## Contrato de entrada

```text
contract: ogv-import-plan-v3
source.type: prepared-offline-game-directory-v1
```

Se presupone que el desacople se realizó antes:

```text
Steamworks → Goldberg/gbe_fork o no requerido
SteamStub  → Steamless o no requerido
DRM de terceros → declarado ausente
```

El importer registra esta frontera y no ejecuta ninguna de esas operaciones.

## Neutralización

El objeto inmutable contiene:

```text
payload/game/
payload/prefix-template/
NEUTRAL_LAYOUT.json
INVENTORY.json
INVENTORY_SEAL.json
```

Estado, documentación, extras y runner se copian por separado durante
`prepare`. El commit trabaja desde el workspace y no desde las rutas fuente.

## Nomenclatura

`naming.py` genera sugerencias conservadoras, ASCII y editables. Los ID de
perfil canónicos son:

```text
linux-bottles-flatpak
linux-direct-wine
linux-umu-proton
windows-native
```

La sugerencia de ejecutable solo se aplica automáticamente cuando hay un único
candidato. Con varios ejecutables, el usuario decide.

## Privacidad

El workspace conserva dos planes:

```text
IMPORT_PLAN.json         privado y operativo
PUBLIC_IMPORT_PLAN.json  publicable y sin rutas del anfitrión
```

El commit publica únicamente el segundo. También elimina del plan público el
comando y checkout local del core.

## Documentación

Los documentos seleccionados se copian al workspace y después a `docs/`.
Los cuatro roles raíz reciben plantilla solamente cuando no se aportó un
documento explícito:

```text
readme
game_sheet
credits
preserved_by
```

## Perfiles

La cápsula puede declarar Bottles, Direct-Wine, UMU y Windows. Cada perfil
mantiene su ID editable y su estado independiente. El importer prohíbe publicar
`verified`.

## Límites

La importación no prueba:

- arranque;
- ausencia material de DRM adicional;
- contenido DLC;
- partidas;
- aislamiento;
- restauración;
- compatibilidad futura.

Esos resultados pertenecen a aceptación funcional posterior.
