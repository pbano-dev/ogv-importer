# Validación — OfflineGameVault Importer 0.3.1

## Ejecución

```bash
bash scripts/test.sh
```

Resultado esperado:

```text
Ran 26 tests
OK
```

## Cobertura principal

- contrato único `ogv-import-plan-v3`;
- origen `prepared-offline-game-directory-v1`;
- nomenclatura portable;
- inspección de ejecutables y `steam_api*.dll`;
- creación de workspace neutral;
- inventario y sello;
- partidas y save-sets;
- destino `unbound`;
- contenido adicional;
- documentación seleccionada sin reescritura;
- plantillas documentales de reserva;
- copia del runner al workspace;
- `PUBLIC_IMPORT_PLAN.json` sin rutas fuente;
- verificación;
- dry-run con y sin estado persistente declarado;
- commit transaccional;
- rollback;
- deduplicación de runner;
- rechazo de traversal, symlinks inseguros y archivos especiales.

## Prueba end-to-end añadida

La suite prepara un juego desacoplado de prueba con:

```text
ejecutable
steam_api64.dll activa
DLL original conservada
partida manual
artbook
README seleccionado
```

Después elimina todas las rutas fuente y completa el commit únicamente desde el
workspace. Finalmente verifica el README exacto, el contenido adicional y la
ausencia de rutas privadas en la evidencia publicada. Una prueba separada
confirma que una cápsula sin partidas ni otro estado omite correctamente el
backup de estado, en lugar de fabricar uno vacío.

## No probado en este entorno

- renderizado interactivo GTK4;
- integración con un Vault real distinto del fixture;
- juego comercial real;
- gameplay;
- Bottles real;
- UMU real;
- Windows nativo.

La GUI se compila e importa sin GTK, pero su interacción debe probarse en el
entorno Bazzite/Fedora objetivo.
