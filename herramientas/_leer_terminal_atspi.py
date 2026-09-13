"""Lee el contenido de la terminal activa vía AT-SPI (accesibilidad de
escritorio), si la app que tiene el foco en este instante expone un nodo
de rol "terminal" -- que es el caso de las terminales nativas de GTK/Qt
(ptyxis, GNOME Terminal, Konsole), no el de las que dibujan su propia UI
con un motor gráfico propio (Warp, Alacritty, Kitty): esas ni siquiera
aparecen en el árbol de AT-SPI.

Corre bajo el Python del **sistema**, no el venv del proyecto: PyGObject
(`gi`) no está en el venv (ver CLAUDE.md) y agregarlo tira de libs de
sistema (girepository) que ya están resueltas para el intérprete del
sistema -- más simple invocarlo por subprocess, mismo patrón que ya usa
el proyecto con playerctl/wmctrl/wl-paste en vez de sumar dependencias
Python. Se llama con la ruta absoluta al intérprete, no "python3" a
secas: dentro de `uv run` el PATH puede resolver al del venv.

Salida: el texto por stdout y código 0 si encontró una terminal activa
con contenido; código 1 sin nada en stdout si la ventana activa no es
una terminal reconocida (el llamador cae a otro camino, no es un error).
"""

import sys

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception:
    sys.exit(1)

_LIMITE_CHARS = 4000
_PROFUNDIDAD_MAX = 40  # cota de seguridad, no un árbol real tan hondo


def _buscar_nodo_terminal(obj, profundidad=0):
    if profundidad > _PROFUNDIDAD_MAX:
        return None
    try:
        if "terminal" in (obj.get_role_name() or "").lower():
            return obj
    except Exception:
        return None
    try:
        cantidad_hijos = obj.get_child_count()
    except Exception:
        return None
    for i in range(cantidad_hijos):
        try:
            hijo = obj.get_child_at_index(i)
        except Exception:
            continue
        if hijo is None:
            continue
        encontrado = _buscar_nodo_terminal(hijo, profundidad + 1)
        if encontrado is not None:
            return encontrado
    return None


def _ventana_activa():
    """La app+frame con foco ahora mismo, o (None, None) si no se pudo
    determinar. No depende de wmctrl/D-Bus de gnome-shell: el propio
    estado ACTIVE de AT-SPI ya lo sabe."""
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None:
            continue
        try:
            cantidad_frames = app.get_child_count()
        except Exception:
            continue
        for j in range(cantidad_frames):
            try:
                frame = app.get_child_at_index(j)
            except Exception:
                continue
            if frame is None:
                continue
            try:
                if frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                    return app, frame
            except Exception:
                continue
    return None, None


def main() -> int:
    _app, frame = _ventana_activa()
    if frame is None:
        return 1
    nodo = _buscar_nodo_terminal(frame)
    if nodo is None:
        return 1
    try:
        texto = Atspi.Text.get_text(nodo, 0, -1)
    except Exception:
        return 1
    texto = (texto or "").strip()
    if not texto:
        return 1
    sys.stdout.write(texto[-_LIMITE_CHARS:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
