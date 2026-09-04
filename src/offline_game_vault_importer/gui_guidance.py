from __future__ import annotations

from collections.abc import Mapping
from typing import Final


FIELD_GUIDANCE: Final[dict[str, dict[str, str]]] = {
    "vault": {
        "example": "/home/usuario/Vaults/offline-games",
        "help": (
            "Colección OfflineGameVault de destino. Debe existir y contener "
            "INDEX.json, COLLECTION_LAYOUT.json y 01_IMMUTABLE_VAULT."
        ),
    },
    "workspace": {
        "example": "/home/usuario/OGV-Workspaces/elden-ring-import",
        "help": (
            "Carpeta temporal y privada que el importer creará. Durante la "
            "preparación contiene una copia verificable y el archivo inmutable; "
            "por eso necesita aproximadamente el doble del tamaño del juego. "
            "La ruta final no debe existir todavía."
        ),
    },
    "manual_game": {
        "example": "/mnt/games/ELDEN RING/Game",
        "help": (
            "Carpeta preparada que contiene el juego jugable y aislado de su "
            "cliente de tienda. Si el ejecutable está dentro de esta carpeta, "
            "su ruta relativa será, por ejemplo, eldenring.exe."
        ),
    },
    "manual_prefix": {
        "example": "/home/usuario/.local/share/bottles/bottles/elden-ring",
        "help": (
            "Prefix Wine/Proton/Bottles existente, normalmente con drive_c y "
            "archivos de registro. Déjelo vacío para un juego autocontenido; "
            "no es necesario para importar partidas externas."
        ),
    },
    "core_root": {
        "example": "/home/usuario/Development/offline-game-vault",
        "help": (
            "Checkout local del proyecto oficial OfflineGameVault Core. El "
            "importer exige la versión 0.19.7 o posterior."
        ),
    },
    "title": {
        "example": "Elden Ring",
        "help": "Título visible con el que aparecerá el juego en el Vault.",
    },
    "edition": {
        "example": "Shadow of the Erdtree Edition",
        "help": (
            "Edición concreta preservada. Use Standard si no corresponde una "
            "edición comercial o recopilatoria específica."
        ),
    },
    "store": {
        "example": "Steam",
        "help": (
            "Procedencia legítima del juego. Es metadato histórico y no crea "
            "una dependencia con el cliente de la tienda."
        ),
    },
    "appid": {
        "example": "1245620",
        "help": (
            "Identificador numérico de la aplicación en la tienda de origen. "
            "Puede dejarse vacío cuando no exista."
        ),
    },
    "version": {
        "example": "1.17",
        "help": (
            "Versión realmente presente en los archivos seleccionados, no la "
            "última versión publicada en Internet."
        ),
    },
    "capsule_id": {
        "example": "steam-1245620-elden-ring-sote-1.17",
        "help": (
            "Identificador técnico único y portable de esta preservación. Use "
            "minúsculas, números, puntos y guiones; incluya versión o edición "
            "si piensa preservar varias variantes."
        ),
    },
    "entrypoint": {
        "example": "eldenring.exe",
        "help": (
            "Ruta del ejecutable relativa al directorio de juego seleccionado. "
            "Si seleccionó la carpeta superior, podría ser Game/eldenring.exe."
        ),
    },
    "game_destination": {
        "example": "drive_c/Games/elden-ring",
        "help": (
            "Ruta relativa dentro del prefix que se creará al materializar. No "
            "introduzca una ruta /home/... ni una letra de unidad de Windows."
        ),
    },
    "working_directory": {
        "example": "drive_c/Games/elden-ring",
        "help": (
            "Directorio relativo desde el que debe arrancarse el ejecutable. "
            "Normalmente coincide con la carpeta que contiene el .exe."
        ),
    },
    "runner_source": {
        "example": "/home/usuario/Runners/GE-Proton9-27",
        "help": (
            "Runner Wine o Proton que desea preservar como objeto reutilizable. "
            "Es opcional y no quedará vinculado a este juego."
        ),
    },
    "runner_id": {
        "example": "ge-proton9-27",
        "help": "Identificador portable sugerido para el runner preservado.",
    },
    "runner_sha256": {
        "example": "Déjelo vacío para calcularlo automáticamente",
        "help": (
            "SHA-256 esperado del runner. Úselo para comprobar una identidad "
            "conocida; si se deja vacío, el importer lo calculará."
        ),
    },
    "supplemental_source": {
        "example": "/home/usuario/Extras/Elden Ring/OST",
        "help": (
            "Archivo o carpeta de contenido adicional poseído, como una banda "
            "sonora, artbook, manual o fondos de pantalla."
        ),
    },
    "supplemental_id": {
        "example": "elden-ring-soundtrack",
        "help": "Identificador portable y único de este contenido adicional.",
    },
    "supplemental_class": {
        "example": "soundtrack",
        "help": (
            "Clasificación descriptiva. Ejemplos: soundtrack, artbook, manual, "
            "wallpapers o video."
        ),
    },
    "documentation_source": {
        "example": "/home/usuario/Documentos/elden-ring-notas.md",
        "help": "Documento que desea incorporar como evidencia o referencia.",
    },
    "documentation_id": {
        "example": "elden-ring-technical-notes",
        "help": "Identificador portable y único del documento.",
    },
    "documentation_role": {
        "example": "technical_notes",
        "help": (
            "Función del documento: readme, game_sheet, credits, preserved_by, "
            "technical_notes u other."
        ),
    },
    "documentation_name": {
        "example": "NOTAS_TECNICAS_DEL_PROCESO.md",
        "help": "Nombre portable con el que se publicará el documento.",
    },
    "state_source": {
        "example": "/prefix/drive_c/users/steamuser/AppData/Roaming/EldenRing/76561198000000000",
        "help": (
            "Archivo o carpeta real que desea preservar como partida, identidad "
            "o configuración. Para Elden Ring conviene seleccionar la carpeta "
            "completa del SteamID."
        ),
    },
    "state_id": {
        "example": "elden-ring-save",
        "help": "Identificador portable y único de este elemento de estado.",
    },
    "state_destination": {
        "example": "drive_c/users/steamuser/AppData/Roaming/EldenRing/76561198000000000",
        "help": (
            "Ruta relativa dentro del futuro prefix donde se restaurará el "
            "archivo o directorio. No incluya una ruta local /home/usuario/..."
        ),
    },
    "save_set_id": {
        "example": "elden-ring-main",
        "help": (
            "Agrupa varios archivos que forman una misma partida atómica. Use "
            "el mismo valor para el .sl2 y otros compañeros seleccionados."
        ),
    },
    "save_display": {
        "example": "Partida principal de Elden Ring",
        "help": "Nombre legible que verá el usuario al elegir una partida.",
    },
}


CHOICE_GUIDANCE: Final[dict[str, dict[str, str]]] = {
    "runner_binding": {
        "example": "Elegir al materializar",
        "help": (
            "El importer conserva la fuente sin vincular un runner. La GUI "
            "oficial permitirá elegir uno compatible al materializar."
        ),
    },
    "baseline_state": {
        "example": "Limpio si el directorio del juego no contiene partidas",
        "help": (
            "Indica si el objeto principal ya contiene estado privado. Elija "
            "Limpio solo cuando haya comprobado que las partidas se importan "
            "por separado."
        ),
    },
    "state_disposition": {
        "example": "Partida restaurable",
        "help": (
            "Define cómo tratar el elemento: partida, identidad, configuración, "
            "exclusión, contenido ya incrustado o estado todavía sin destino."
        ),
    },
}


ALL_GUIDED_FIELDS: Final[frozenset[str]] = frozenset(
    {*FIELD_GUIDANCE, *CHOICE_GUIDANCE}
)


PENDING_COMPONENT_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "partida o estado": (
        "state_source", "state_id", "state_destination",
        "save_set_id", "save_display",
    ),
    "contenido adicional": ("supplemental_source", "supplemental_id"),
    "documentación": (
        "documentation_source", "documentation_id", "documentation_name",
    ),
}


def pending_component_sections(values: Mapping[str, str]) -> list[str]:
    """Return staged GUI sections that have not been added to the plan."""

    return [
        label
        for label, keys in PENDING_COMPONENT_FIELDS.items()
        if any(values.get(key, "").strip() for key in keys)
    ]
