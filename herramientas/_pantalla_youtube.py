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

Cambiar de canal navega la MISMA pestaña por CDP (`--remote-debugging-
port`, sólo en localhost) en vez de matar la ventana y abrir una nueva
-- versión anterior de este archivo hacía lo segundo, y el usuario
preguntó por qué no reusar la pestaña. Tenía razón, y no era solo un
tema de prolijidad: matar y reabrir le cambiaba la identidad al stream
de audio en PipeWire en cada cambio de canal, lo que rompió de verdad el
ducking (`herramientas/_ducking.py`) -- el volumen quedaba pegado en el
10% duckeado después de cambiar de canal a mitad de una conversación,
porque el Ducker seguía trackeando el nodo viejo, ya muerto. Reusar la
pestaña elimina la causa de raíz en vez de parchear el síntoma: el
stream de audio nunca cambia de identidad entre canales, porque nunca
se cierra el proceso. Si por lo que sea CDP falla (la ventana no está
abierta, el usuario la cerró a mano), se cae al camino viejo de abrir
una ventana nueva.
"""

import asyncio
import json as _json
import re
import subprocess
import time
from pathlib import Path

import httpx
import websockets

PERFIL = Path.home() / ".config" / "tero" / "chrome_youtube"

# Solo localhost (default de Chrome al no pasar --remote-debugging-address),
# así que no expone nada fuera de esta máquina.
_PUERTO_CDP = 9333

# Chrome pone esto al final del título de toda ventana; lo que interesa
# para mostrar en el soul-connector es lo que queda antes.
_SUFIJOS_TITULO = (" - YouTube - Google Chrome", " - Google Chrome")

# Geometría fija del monitor donde vive "YouTube" en este escritorio (el
# Samsung C24FG70, arriba a la izquierda -- confirmado en vivo con el
# usuario probando distintas posiciones). Si algún día cambia el arreglo
# de monitores, `xrandr` muestra la geometría real de cada uno.
MONITOR = {"x": 0, "y": 0, "ancho": 1920, "alto": 1080}


def mostrar(url: str) -> None:
    """Muestra `url` en la ventana dedicada, en el monitor de YouTube.

    Si la ventana ya está abierta, la navega en el lugar (CDP) -- mismo
    proceso, mismo stream de audio, sin parpadeo. Si no hay ventana viva
    (primera vez, o CDP falló por lo que sea), abre una de cero."""
    if _navegar_por_cdp(url):
        return
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
            f"--remote-debugging-port={_PUERTO_CDP}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _reposicionar(proceso.pid)


def _navegar_por_cdp(url: str) -> bool:
    """True si logró navegar una pestaña ya abierta a `url` por el
    protocolo de depuración remota de Chrome (CDP). False ante cualquier
    problema (ventana no abierta, puerto no responde, sin pestañas) --
    en ese caso `mostrar()` cae a abrir una ventana nueva."""
    try:
        pestanas = httpx.get(f"http://127.0.0.1:{_PUERTO_CDP}/json", timeout=2.0).json()
    except Exception:
        return False
    pestana = next((p for p in pestanas if p.get("type") == "page"), None)
    url_debug = pestana.get("webSocketDebuggerUrl") if pestana else None
    if not url_debug:
        return False
    try:
        asyncio.run(_enviar_navigate(url_debug, url))
        return True
    except Exception:
        return False


async def _enviar_navigate(url_debug: str, url: str) -> None:
    async with websockets.connect(url_debug) as ws:
        await ws.send(_json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        await ws.recv()


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


def _reproductor_mpris() -> str | None:
    """Nombre del reproductor MPRIS (`playerctl -l`) de la ventana
    dedicada, si está abierta y con algo de media activo. Chrome expone
    cada ventana con audio/video como un reproductor MPRIS aparte
    (`chromium.instance<PID>`) -- y el PID en el nombre es justo el del
    proceso que lanzó `mostrar()`, así que alcanza con el mismo filtro
    por `--user-data-dir` que usa `titulo_actual()`, sin tener que
    recordar el PID entre llamadas."""
    try:
        salida = subprocess.run(
            ["playerctl", "-l"], capture_output=True, text=True, timeout=2.0, check=False
        ).stdout
    except Exception:
        return None
    for nombre in salida.splitlines():
        nombre = nombre.strip()
        match = re.match(r"^chromium\.instance(\d+)$", nombre)
        if match and _es_nuestro_proceso(match.group(1)):
            return nombre
    return None


def pausar() -> None:
    """Pausa lo que esté sonando en la ventana de YouTube, si hay algo.
    No hace nada (silencioso) si la ventana no está abierta o no tiene
    media activo -- se llama desde herramientas/musica.py cada vez que
    arranca algo de música, para que no suenen las dos cosas juntas."""
    nombre = _reproductor_mpris()
    if nombre:
        subprocess.run(["playerctl", "-p", nombre, "pause"], check=False)


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
                # Maximizar de verdad (estado de ventana que maneja
                # mutter), no solo pedir el tamaño del monitor a mano --
                # así no depende de que MONITOR tenga la resolución
                # exacta bien puesta (con decoraciones, escala, etc. el
                # tamaño "a ojo" quedaba con margen visible alrededor,
                # reportado en vivo por el usuario).
                subprocess.run(
                    ["wmctrl", "-ir", partes[0], "-b",
                     "add,maximized_vert,maximized_horz"],
                    check=False,
                )
                return
