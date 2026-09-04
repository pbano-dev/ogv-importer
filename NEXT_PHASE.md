# Siguiente fase

## Prioridad inmediata

1. Probar la GUI 0.5.0a1 en Bazzite/Fedora con GTK4.
2. Importar un juego comercial pequeño ya aislado de su tienda.
3. Confirmar que aparece en Offline Game Vault GUI.
4. Materializarlo desde `game-source` con Bottles, Direct-Wine y UMU usando
   runners preservados compatibles.
5. Repetir las composiciones con BSO o artbook seleccionable.
6. Validar partida, aislamiento de red, cierre y restauración limpia.

## Evolución arquitectónica

- mover la actualización completa del control plane del Vault desde
  `vault_commit.py` a una operación pública transaccional del Core;
- migrar la presentación del importer al mismo toolkit Qt/PySide6 que la GUI
  oficial sin mezclar lógica de dominio con widgets;
- permitir reordenar y retirar adjuntos ya seleccionados;
- mostrar un inventario previo a la preparación;
- detectar cambios en el árbol fuente durante copias largas;
- añadir un recibo gráfico de aceptación funcional posterior.

Ninguno de estos puntos debe introducir selección de backend o runner durante
la importación. Esa decisión sigue perteneciendo a la materialización.
