"""Ventana del soul-connector: proceso aparte, cliente opcional del stream
de niveles. El daemon (main.py) tiene que funcionar sin esto -- correlo vos
por separado cuando quieras verlo:

    uv run python -m soul_connector.ventana

No hay "siempre encima" real en Wayland/GNOME sin gtk-layer-shell (que
necesita paquetes de sistema y no hay garantía de que Mutter lo soporte
bien para apps normales). Se optó por algo más simple: la ventana no roba
foco nunca (focus=False), y se trae al frente sola justo cuando arranca a
hablar (ver traer_al_frente, llamado desde soul_connector/index.html por WebSocket).
Alcanza: solo importa que se vea mientras habla, no el resto del tiempo.
"""

import subprocess
from pathlib import Path

import webview

_TITULO_VENTANA = "tero-soul-connector"

_RUTA_HTML = Path(__file__).parent / "index.html"
_RUTA_SIRIWAVE = Path(__file__).parent / "siriwave.umd.js"
_ANCHO, _ALTO = 260, 74
_MARGEN = 20

def _html_con_js_incrustado() -> str:
    # Con url= (sirviendo el archivo vía el servidor Bottle interno de
    # pywebview) las opciones de tamaño/posición no se respetaban bien acá
    # -- con html= sí (mismo camino que el POC que anduvo). Se incrusta
    # siriwave.umd.js inline para no depender de que el <script src=...>
    # relativo resuelva bien sin una URL base real.
    html = _RUTA_HTML.read_text()
    js = _RUTA_SIRIWAVE.read_text()
    return html.replace(
        '<script src="siriwave.umd.js"></script>', f"<script>{js}</script>"
    )


class _API:
    def __init__(self, ventana: "webview.Window"):
        self._ventana = ventana
        self._id_ventana: str | None = None

    def traer_al_frente(self) -> None:
        # El flag on_top de Qt (ver platforms/qt.py, set_on_top) no se
        # sostiene: Mutter lo deja de respetar en cuanto el usuario foca
        # otra ventana. wmctrl le pide al gestor de ventanas el mismo
        # estado ("above") que pondría el menú "Siempre encima" del
        # usuario -- se sostiene mejor porque pasa por ese otro camino.
        # Se mantienen ambos: wmctrl como intento principal, el flag de
        # Qt como respaldo si wmctrl no está o falla.
        self._ventana.on_top = True
        self._ventana.show()
        if self._id_ventana is None:
            self._id_ventana = _buscar_id_ventana()
        if self._id_ventana is not None:
            subprocess.run(
                ["wmctrl", "-i", "-r", self._id_ventana, "-b", "add,above"],
                check=False,
            )


def _buscar_id_ventana() -> str | None:
    try:
        resultado = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    for linea in resultado.stdout.splitlines():
        if _TITULO_VENTANA in linea:
            return linea.split()[0]
    return None


def _posicion_esquina_inferior_derecha() -> tuple[int, int]:
    pantallas = webview.screens
    principal = pantallas[0] if pantallas else None
    if principal is None:
        return (100, 100)
    x = principal.x + principal.width - _ANCHO - _MARGEN
    y = principal.y + principal.height - _ALTO - _MARGEN
    return (x, y)


def main() -> None:
    x, y = _posicion_esquina_inferior_derecha()
    ventana = webview.create_window(
        _TITULO_VENTANA,
        html=_html_con_js_incrustado(),
        width=_ANCHO,
        height=_ALTO,
        x=x,
        y=y,
        frameless=True,
        # Mutter no deja que la app se reposicione sola (ni el x/y de
        # creación ni wmctrl -e sirven, probado con _MARGEN=400 sin
        # ningún cambio visual). easy_drag=True es la única forma real de
        # moverla: clickeás y arrastrás en cualquier parte de la ventana.
        easy_drag=True,
        focus=False,
        on_top=False,
        transparent=True,
        resizable=False,
        shadow=False,
    )
    ventana.expose(_API(ventana).traer_al_frente)
    webview.start(gui="qt")


if __name__ == "__main__":
    main()
