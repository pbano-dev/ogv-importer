# Validación — OfflineGameVault Importer 0.5.0a1

## Suite aislada

```bash
bash scripts/test.sh
```

La suite cubre el plan neutral v4, inspección, nomenclatura, workspace
determinista, privacidad, estado persistente, contenido adicional, dry-run,
commit, rollback y rechazo de rutas o tipos inseguros. También prueba el flujo
completo que usa la GUI: preparar, verificar, ensayar e importar.

## Contrato con el Core actual

```bash
./scripts/check-core-contract.sh \
  ../offline-game-vault \
  ../offline-game-vault-gui
```

Esta prueba usa el checkout indicado, no un doble simulado. Verifica:

- versión Core 0.19.7 o posterior;
- presencia de los comandos públicos requeridos;
- importación real de una cápsula `game-source`;
- aceptación de la cápsula por `audit-capsule`;
- lectura del contenido adicional por `list-optional-content`;
- disponibilidad del objeto opcional en el CAS;
- composición Direct-Wine real con un runner preservado y el artbook elegido;
- descubrimiento de la cápsula por el catálogo de la GUI oficial.

## Cobertura principal

- contrato único `ogv-import-plan-v4`;
- origen `prepared-offline-game-directory-v1` independiente de tiendas;
- único perfil publicable `game-source` con `adapter=other`;
- runner desacoplado y seleccionable al materializar;
- contenido adicional como objetos inmutables independientes;
- `PUBLIC_IMPORT_PLAN.json` sin rutas fuente ni configuración local del Core;
- rechazo temprano de un Core antiguo o incompleto;
- publicación transaccional y rollback.

## Validación todavía manual

- renderizado e interacción GTK4 en Bazzite/Fedora;
- importación de un juego comercial real ya preparado;
- materialización y gameplay real con Bottles, Direct-Wine y UMU;
- partidas, DLC, audio, vídeo, mando, red, cierre y restauración.

La importación solo demuestra estructura, integridad y compatibilidad de
contrato. La aceptación funcional pertenece a cada materialización.
