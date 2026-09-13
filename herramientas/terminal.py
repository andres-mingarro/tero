"""Leer la terminal activa, sin que el usuario tenga que copiar nada a
mano si está en una terminal nativa (GTK/Qt): se lee por accesibilidad
(AT-SPI, ver `_leer_terminal_atspi.py`), que expone el contenido de
widgets nativos como si fuera para un lector de pantalla. No sirve con
terminales de motor gráfico propio (Warp, Alacritty, Kitty): esas ni
aparecen en el árbol de accesibilidad.

Para esos casos el respaldo es la **selección primaria** de X/Wayland
-- lo que queda resaltado con el mouse, sin apretar Ctrl+C -- y nada
más. A propósito NO se revisa el portapapeles de Ctrl+C: el usuario
puede tener ahí copiada otra cosa para un uso distinto, y no quiere que
Tero se la lleve puesta.
"""

import subprocess
from pathlib import Path

from herramientas import herramienta

_LIMITE_CHARS = 4000
_SCRIPT_ATSPI = Path(__file__).parent / "_leer_terminal_atspi.py"
_PYTHON_SISTEMA = "/usr/bin/python3"


def _desde_atspi() -> str | None:
    try:
        resultado = subprocess.run(
            [_PYTHON_SISTEMA, str(_SCRIPT_ATSPI)],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return resultado.stdout if resultado.returncode == 0 else None


def _desde_seleccion_primaria() -> str | None:
    for comando in (["wl-paste", "--primary"], ["xclip", "-o", "-selection", "primary"]):
        try:
            resultado = subprocess.run(comando, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if resultado.returncode == 0 and resultado.stdout.strip():
            return resultado.stdout
    return None


@herramienta
def leer_terminal() -> str:
    """Lee el contenido de la terminal activa: por accesibilidad si es una
    terminal nativa (ptyxis, GNOME Terminal), o lo último resaltado con el
    mouse (selección primaria) si no."""
    contenido = _desde_atspi() or _desde_seleccion_primaria()
    if not contenido:
        return (
            "No pude leer nada: la ventana activa no es una terminal que pueda "
            "leer directamente, y no hay nada resaltado con el mouse."
        )
    return contenido.strip()[-_LIMITE_CHARS:]
