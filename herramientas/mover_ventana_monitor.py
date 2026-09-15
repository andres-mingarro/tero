"""Mover la ventana de una app a otro monitor del escritorio.

Por qué no wmctrl: wmctrl solo puede tocar ventanas X11/XWayland desde
afuera del compositor. Bajo Wayland (como corre este escritorio, ver
CLAUDE.md) la mayoría de las apps nativas simplemente lo ignoran -- mismo
problema que ya tenía herramientas/_pantalla_youtube.py, ahí esquivado
forzando --ozone-platform=x11 al lanzar Chrome. Esto generaliza: llamar a
Meta.Window.move_to_monitor() desde ADENTRO del compositor -- un método
D-Bus expuesto por soul-connector-gnome/extension.js, que ya corre como
parte del shell -- no le importa si la ventana es X11 o Wayland nativa.

Validado en vivo antes de escribir esto: se abrió una terminal vacía
dentro de un shell anidado (gnome-shell --devkit) y se la movió de
monitor por este mismo método, confirmado con el monitor antes/después.
Ver memoria del proyecto, entrada "mover ventana a monitor".

Requiere que soul-connector-gnome esté cargada con este método (después
de un logout si se editó extension.js después del último arranque de
sesión -- GNOME cachea el código, ver README de esa carpeta).
"""

import json
import re
import subprocess

from herramientas import herramienta

_BUS = "org.gnome.Shell"
_PATH = "/org/gnome/Shell/Extensions/Tero"
_IFAZ = "org.gnome.Shell.Extensions.Tero"


def _llamar(metodo: str, *args: str) -> dict:
    proceso = subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", _BUS,
            "--object-path", _PATH,
            "--method", f"{_IFAZ}.{metodo}",
            *args,
        ],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if proceso.returncode != 0:
        error = proceso.stderr.strip()
        if "UnknownMethod" in error or "UnknownObject" in error:
            return {
                "ok": False,
                "error": "la extensión de Tero en GNOME todavía no tiene esta "
                "función cargada (hace falta cerrar sesión y volver a entrar)",
            }
        return {"ok": False, "error": error}
    coincidencia = re.search(r"^\(\s*'(.*)'\s*,?\)\s*$", proceso.stdout.strip(), re.DOTALL)
    if not coincidencia:
        return {"ok": False, "error": f"respuesta inesperada de D-Bus: {proceso.stdout!r}"}
    return json.loads(coincidencia.group(1))


@herramienta
def mover_ventana_a_monitor(app: str, monitor: int) -> str:
    """Mueve la ventana de una app a otro monitor del escritorio.

    app: nombre de la app o parte del título de su ventana (ej. "chrome",
    "terminal", "spotify", "code").
    monitor: número de monitor, empezando en 1.
    """
    resultado = _llamar("MoverVentanaAMonitor", app, str(monitor - 1))
    if not resultado.get("ok"):
        error = resultado.get("error", "error desconocido")
        if "no encontrada" in error:
            return f"No encontré ninguna ventana de {app!r} abierta."
        if error.startswith("no hay monitor"):
            return f"Este escritorio no tiene el monitor {monitor}."
        return f"No pude mover la ventana: {error}"
    return f"Movida la ventana de {resultado['titulo']!r} al monitor {monitor}."
