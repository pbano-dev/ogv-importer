# Validación 0.2.1

## Automatizada

Suite incluida: **21 pruebas**.

```bash
./scripts/test.sh
```

Cubre:

- traversal y symlinks inseguros;
- escaneo de paquetes históricos;
- preparación legacy y manual;
- saneamiento de rutas del anfitrión;
- estado parcial no bloqueante;
- save-sets multielemento;
- runner ausente;
- runner nuevo seleccionado como directorio;
- deduplicación por SHA-256;
- dry-run;
- commit sintético y recibos;
- integridad del manifiesto de colección.

## Antes de un commit real

```text
[ ] copia externa del Vault
[ ] espacio temporal suficiente
[ ] workspace verificado
[ ] privacidad revisada o excepción declarada
[ ] IMPORT_PLAN sin marcadores obligatorios
[ ] ensayo commit-vault --dry-run
[ ] núcleo oficial accesible
```

## Después del commit

```text
[ ] juego visible en la GUI principal
[ ] perfil Bottles materializable
[ ] perfil Direct-Wine materializable
[ ] exportación Windows generable
[ ] sin partida, cuando baseline_state=clean
[ ] save-set multielemento aplicado completo
[ ] partida cargada
[ ] cierre normal
[ ] retirada y restauración limpia
```

Una importación estructural aprobada no equivale a aceptación funcional.
