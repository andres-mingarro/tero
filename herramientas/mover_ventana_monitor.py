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

from herramientas import herramienta
from herramientas._gnome_dbus import llamar


@herramienta
def mover_ventana_a_monitor(app: str, monitor: int) -> str:
    """Mueve la ventana de una app a otro monitor del escritorio.

    app: nombre de la app o parte del título de su ventana (ej. "chrome",
    "terminal", "spotify", "code").
    monitor: número de monitor, empezando en 1.
    """
    resultado = llamar("MoverVentanaAMonitor", app, str(monitor - 1))
    if not resultado.get("ok"):
        error = resultado.get("error", "error desconocido")
        if "no encontrada" in error:
            return f"No encontré ninguna ventana de {app!r} abierta."
        if error.startswith("no hay monitor"):
            return f"Este escritorio no tiene el monitor {monitor}."
        return f"No pude mover la ventana: {error}"
    return f"Movida la ventana de {resultado['titulo']!r} al monitor {monitor}."
