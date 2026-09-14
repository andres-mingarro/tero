"""Ventana de Chrome dedicada para mostrar YouTube en un monitor fijo (el
"monitor de YouTube" del escritorio del usuario).

Por qué un perfil de Chrome aparte, forzado a XWayland: el Chrome que el
usuario usa a diario corre nativo en Wayland, y un cliente Wayland nativo
no deja que ningún programa externo le diga en qué monitor abrirse --
probado en vivo, `--window-position` no tenía ningún efecto y la ventana
terminaba siempre en el monitor activo, sin importar el valor pedido.
Forzando `--ozone-platform=x11` (con `--user-data-dir` propio, así arranca
un proceso nuevo de verdad en vez de pedirle una ventana al Chrome que ya
está corriendo, que heredaría su mismo backend Wayland) la ventana pasa a
ser una ventana X11/XWayland real, que mutter sí deja mover -- mismo
patrón que ya usaba `soul_connector/ventana.py` (`QT_QPA_PLATFORM=xcb`)
para lo mismo.

Tampoco alcanza con `--window-position` en el lanzamiento: se probó en
vivo y la ventana aparece en el monitor por defecto igual, ignorando el
flag. Lo que sí funciona, confirmado en vivo: dejar que abra donde quiera
y después moverla con `wmctrl -ir <ventana> -e ...`, ubicando la ventana
por el PID del proceso recién lanzado (no por título -- el título cambia
con cada URL/canal, el PID no).

Cada "cambio de canal" mata la ventana anterior y abre una nueva. No hay
protocolo de control remoto (CDP) para navegar la ventana existente en
vez de reabrirla, y no hace falta: es un cambio de canal, no una sesión
de navegación que haya que preservar entre pedidos.
"""

import re
import subprocess
import time
from pathlib import Path

PERFIL = Path.home() / ".config" / "tero" / "chrome_youtube"

# Chrome pone esto al final del título de toda ventana; lo que interesa
# para mostrar en el soul-connector es lo que queda antes.
_SUFIJOS_TITULO = (" - YouTube - Google Chrome", " - Google Chrome")

# Geometría fija del monitor donde vive "YouTube" en este escritorio (el
# Samsung C24FG70, arriba a la izquierda -- confirmado en vivo con el
# usuario probando distintas posiciones). Si algún día cambia el arreglo
# de monitores, `xrandr` muestra la geometría real de cada uno.
MONITOR = {"x": 0, "y": 0, "ancho": 1920, "alto": 1080}


def mostrar(url: str) -> None:
    """Abre `url` en la ventana dedicada, en el monitor de YouTube."""
    _cerrar()
    PERFIL.mkdir(parents=True, exist_ok=True)
    proceso = subprocess.Popen(
        [
            "google-chrome",
            "--ozone-platform=x11",
            "--new-window",
            f"--user-data-dir={PERFIL}",
            f"--window-position={MONITOR['x']},{MONITOR['y']}",
            f"--window-size={MONITOR['ancho']},{MONITOR['alto']}",
            # Sin esto, Chrome bloquea el autoplay con sonido en un
            # perfil sin "engagement" previo -- y este perfil, al ser
            # dedicado, nunca lo tiene: cada video quedaba pausado
            # esperando un click. El usuario pidió que arranque solo.
            "--autoplay-policy=no-user-gesture-required",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _reposicionar(proceso.pid)


def _cerrar() -> None:
    subprocess.run(["pkill", "-f", f"user-data-dir={PERFIL}"], check=False)


def _es_nuestro_proceso(pid: str) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
    except OSError:
        return False
    return f"user-data-dir={PERFIL}" in cmdline


def _limpiar_titulo(titulo: str) -> str:
    for sufijo in _SUFIJOS_TITULO:
        if titulo.endswith(sufijo):
            titulo = titulo[: -len(sufijo)]
            break
    # Chrome antepone "(N) " si hay notificaciones/chat sin leer -- no
    # aporta nada acá.
    return re.sub(r"^\(\d+\)\s*", "", titulo).strip()


def titulo_actual() -> str | None:
    """Título de lo que esté mostrando la ventana dedicada ahora mismo --
    lo haya abierto Tero (reproducir_canal_youtube) o el usuario a mano
    navegando adentro de esa misma ventana, da igual: se lee el título
    real, no se recuerda qué se pidió. None si la ventana no está
    abierta."""
    try:
        salida = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, timeout=2.0, check=False
        ).stdout
    except Exception:
        return None
    for linea in salida.splitlines():
        # wmctrl -lp: id_ventana, escritorio, pid, hostname, título.
        partes = linea.split(None, 4)
        if len(partes) < 5:
            continue
        _ventana, _escritorio, pid, _hostname, titulo = partes
        if _es_nuestro_proceso(pid):
            return _limpiar_titulo(titulo)
    return None


def _reposicionar(pid: int, intentos: int = 20, espera_s: float = 0.2) -> None:
    """Espera a que la ventana de `pid` aparezca y la ubica en MONITOR.

    Reintenta porque Chrome tarda en mapear la ventana (más la primera vez
    que arranca un perfil nuevo, que además puede mostrar un diálogo de
    términos del servicio antes que nada -- ese diálogo es otra ventana
    del mismo proceso y también hay que moverla, por eso se sigue
    reposicionando aunque ya se haya movido una vez, no se corta al primer
    éxito."""
    objetivo = f"0,{MONITOR['x']},{MONITOR['y']},{MONITOR['ancho']},{MONITOR['alto']}"
    for _ in range(intentos):
        time.sleep(espera_s)
        salida = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, check=False
        ).stdout
        for linea in salida.splitlines():
            partes = linea.split(None, 3)
            if len(partes) >= 3 and partes[2] == str(pid):
                subprocess.run(
                    ["wmctrl", "-ir", partes[0], "-e", objetivo], check=False
                )
                return
