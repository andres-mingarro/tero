"""Leer la terminal: sin tmux no hay forma limpia de leer el scrollback de
una terminal arbitraria en Linux. Si la sesión corre dentro de tmux se usa
`capture-pane`; si no, se cae al portapapeles (se espera que el usuario
copie el error antes de pedir ayuda), igual que en Windows.
"""

import os
import subprocess

from herramientas import herramienta

_LIMITE_CHARS = 4000


def _desde_tmux() -> str | None:
    if "TMUX" not in os.environ:
        return None
    resultado = subprocess.run(
        ["tmux", "capture-pane", "-p", "-S", "-200"],
        capture_output=True,
        text=True,
        check=False,
    )
    return resultado.stdout if resultado.returncode == 0 else None


def _desde_portapapeles() -> str | None:
    for comando in (["wl-paste"], ["xclip", "-o", "-selection", "clipboard"]):
        try:
            resultado = subprocess.run(comando, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if resultado.returncode == 0 and resultado.stdout.strip():
            return resultado.stdout
    return None


@herramienta
def leer_terminal() -> str:
    """Lee la salida reciente de una terminal (scrollback de tmux o, si no
    hay sesión de tmux, lo último copiado al portapapeles)."""
    contenido = _desde_tmux() or _desde_portapapeles()
    if not contenido:
        return "No pude leer nada: ni estás en tmux ni hay texto copiado en el portapapeles."
    return contenido.strip()[-_LIMITE_CHARS:]
