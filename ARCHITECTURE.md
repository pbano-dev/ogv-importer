# Arquitectura del importador 0.2.1

```text
GUI / CLI
    ↓
ImportSession + IMPORT_PLAN v2
    ↓
scanner / manual selectors
    ↓
prepare legacy | prepare manual
    ↓
workspace neutral sellado
    ↓
validate + dry-run
    ↓
commit transaccional
    ↓
OfflineGameVault
```

## Autoridad

1. selección efectiva del usuario;
2. árbol y hashes de la fuente;
3. detección automática;
4. documentación histórica.

La detección nunca modifica una selección confirmada.

## Objeto canónico

El objeto principal contiene:

```text
neutral-object/
├── payload/game/
├── payload/prefix-template/
├── NEUTRAL_LAYOUT.json
├── INVENTORY.json
└── INVENTORY_SEAL.json
```

No contiene `bottle.yml`, partidas extraídas ni runner.

## Perfiles

La cápsula publica host contracts neutrales:

- `ogv-bottles-neutral-v1`;
- `ogv-direct-wine-neutral-v1`;
- `ogv-windows-export-v1`.

La GUI principal transforma esos contratos al materializar. La aceptación se
vincula al perfil, runner y save-set elegidos.

## Estado

`accepted` representa el baseline importado. Los save-sets se almacenan como
`ogv-save-set-v2` y pueden incluir varios ficheros o directorios. La selección
predeterminada es siempre `none`; solo se rotula “Sin partida” cuando
`baseline_state=clean`.

## Transacción

El commit:

1. relee los documentos críticos;
2. crea staging;
3. audita cápsula y estado con el núcleo;
4. ingiere objetos por digest;
5. publica árboles completos;
6. regenera inventario y manifiestos;
7. revierte cualquier ruta creada si falla.
