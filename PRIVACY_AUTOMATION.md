# Automatización de privacidad

## Objetivo

Convertir las revisiones repetitivas en reglas generales, auditables e
idempotentes. El importador no sustituye valores arbitrarios ni borra secciones
completas del registro.

## Categorías automáticas

### Windows Installer

Cuando el valor contiene una ruta privada del anfitrión:

- elimina `LastUsedSource`;
- elimina entradas de `SourceList\\Net`;
- elimina `InstallSource` en `InstallProperties` y `Uninstall`;
- conserva `PackageName` como basename.

Estas rutas describen la procedencia del instalador, no el estado necesario para
ejecutar el juego ya instalado.

### Fuentes externas

En las claves de fuentes de Windows y Wine:

- si el archivo existe dentro de `drive_c/windows/Fonts`, reescribe la ruta a
  `C:\\windows\\Fonts\\<archivo>`;
- si no existe dentro del prefix, elimina la referencia externa;
- registra `functional_retest_required=true`.

El importador no copia fuentes del anfitrión.

### Desconocido

Una ruta privada situada fuera de las categorías anteriores permanece intacta y
bloquea la aceptación. Debe añadirse una regla nueva solo después de comprender
su función.

## Evidencia

`reports/privacy-sanitization.json` registra:

- fichero;
- sección;
- nombre del valor;
- acción;
- basename cuando aplica;
- SHA-256 antes y después.

No registra el contenido de la ruta privada.

`reports/privacy-report.json` informa de las referencias que siguen presentes.
`blocking_text_hits` cuenta coincidencias reales y `blocking_files` cuenta
ficheros afectados.
