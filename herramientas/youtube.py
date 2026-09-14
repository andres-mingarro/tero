"""Canales de YouTube que el usuario mira seguido: un atajo de voz para
abrirlos en vivo (o avisar qué tienen nuevo) directo en el monitor
dedicado a YouTube, sin tener que buscarlos a mano cada vez.

Los `id` de canal (UCxxxx) se resolvieron a mano desde la etiqueta
`<link rel="canonical">` de la página de cada @handle -- es más confiable
que buscar "channelId" a mano en el HTML, que a veces trae el de un canal
recomendado en vez del propio (pasó con "Parén la Mano" y "Midu" antes de
usar el canonical). Hacen falta para el feed RSS de
`sugerir_canales_youtube`; para abrir el canal en vivo alcanza el handle.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx

from herramientas import _pantalla_youtube, herramienta

_CANALES = {
    "olga": {"handle": "olgaenvivo_", "id": "UC7mJ2EDXFomeDIRFu5FtEbA", "nombre": "Olga"},
    "mitre": {"handle": "Radiomitre", "id": "UCYvINPByAdCcpA0sWrF3I_w", "nombre": "Radio Mitre"},
    "urbana_play": {"handle": "UrbanaPlayFM", "id": "UCC1kfsMJko54AqxtcFECt-A", "nombre": "Urbana Play"},
    "paren_la_mano": {"handle": "Parenlamano", "id": "UCulzKEqyE73gXCUqTimbP4A", "nombre": "Parén la Mano"},
    "aislados": {"handle": "AisladosElPodcast", "id": "UCXjNLHGKB83V7FxRvEeWJ5A", "nombre": "Aislados"},
    "vorterix": {"handle": "VorterixOficial", "id": "UCvCTWHCbBC0b9UIeLeNs8ug", "nombre": "Vorterix"},
    "midu": {"handle": "midudev", "id": "UC8LeXCWOalN8SxlrPcG-PaQ", "nombre": "Midu"},
}

_CanalId = Literal["olga", "mitre", "urbana_play", "paren_la_mano", "aislados", "midu", "vorterix"]

def estado_actual() -> dict | None:
    """Para el soul-connector, igual que hace con la canción de Spotify
    (ver main.py, _actualizar_cancion_soul_connector): {"texto",
    "progreso_ms", "duracion_ms", "reproduciendo"} de lo que esté
    mostrando la ventana de YouTube ahora mismo, o None si no está
    abierta. Se lee el título real de la ventana (_pantalla_youtube.
    titulo_actual()), no se recuerda qué pidió el usuario la última vez
    -- así agarra también lo que el usuario haya abierto a mano adentro
    de esa misma ventana, no solo lo que abrió Tero. Sin duración real
    (es un canal en vivo, no una canción con punta y final) -- el
    soul-connector sabe ocultar la barra de progreso cuando
    `duracion_ms` es 0."""
    titulo = _pantalla_youtube.titulo_actual()
    if not titulo:
        return None
    return {"texto": titulo, "progreso_ms": 0, "duracion_ms": 0, "reproduciendo": True}


@herramienta
def reproducir_canal_youtube(canal: _CanalId) -> str:
    """Abre el canal de YouTube pedido en vivo, en el monitor dedicado a
    YouTube. Usala cuando el usuario nombra un canal puntual ("poné
    Olga", "quiero ver Midu", "dale, Mitre") -- se abre directo, no hace
    falta preguntar nada más."""
    info = _CANALES[canal]
    _pantalla_youtube.mostrar(f"https://www.youtube.com/@{info['handle']}/live")
    return f"Abrí {info['nombre']} en vivo."


@herramienta
def abrir_youtube_general() -> str:
    """Usala cuando el usuario pide YouTube sin decir qué canal ("poné
    algo de youtube", "abrí youtube", "quiero ver algo"). Abre YouTube en
    el monitor dedicado y tu respuesta hablada tiene que preguntarle si
    quiere ver algo específico o si le contás qué canales tienen
    novedades ahora -- esta es la única herramienta para la que vale
    preguntar y esperar la respuesta (ver la excepción en las reglas)."""
    _pantalla_youtube.mostrar("https://www.youtube.com")
    return "Abrí YouTube."


def _esta_en_vivo(handle: str) -> tuple[bool, str | None]:
    """(está en vivo ahora, título) siguiendo el /live del canal -- si no
    hay transmisión, YouTube redirige a la página normal del canal y no
    trae 'isLive':true."""
    try:
        respuesta = httpx.get(
            f"https://www.youtube.com/@{handle}/live",
            headers={"User-Agent": "Mozilla/5.0"},
            follow_redirects=True,
            timeout=6.0,
        )
    except Exception:
        return False, None
    if '"isLive":true' not in respuesta.text:
        return False, None
    match = re.search(r'"title":"([^"]+)"', respuesta.text)
    return True, (match.group(1) if match else None)


def _subio_hace_poco(canal_id: str, horas: int = 20) -> str | None:
    """Título del último video del feed RSS si se publicó hace menos de
    `horas`, o None. Respaldo para canales que no transmiten en vivo
    seguido (ej. Midu, que sube videos sueltos en vez de un programa
    diario) -- para esos, "está en vivo" casi nunca da señal de nada."""
    try:
        respuesta = httpx.get(
            "https://www.youtube.com/feeds/videos.xml",
            params={"channel_id": canal_id},
            timeout=6.0,
        )
        respuesta.raise_for_status()
    except Exception:
        return None
    entradas = respuesta.text.split("<entry>", 1)
    if len(entradas) < 2:
        return None
    entrada = entradas[1]
    match_fecha = re.search(r"<published>([^<]+)</published>", entrada)
    match_titulo = re.search(r"<title>([^<]+)</title>", entrada)
    if not match_fecha or not match_titulo:
        return None
    publicado = datetime.fromisoformat(match_fecha.group(1))
    if datetime.now(timezone.utc) - publicado < timedelta(hours=horas):
        return match_titulo.group(1)
    return None


@herramienta
def sugerir_canales_youtube() -> str:
    """Usala solo cuando el usuario contestó que quiere ver "lo nuevo" o
    "videos nuevos" (respuesta a la pregunta de abrir_youtube_general).
    Devuelve qué canales están en vivo o subieron algo hace poco, para
    que se lo leas y preguntes cuál quiere -- no abre nada todavía, eso
    lo hace reproducir_canal_youtube en el turno siguiente."""
    novedades = []
    for datos in _CANALES.values():
        en_vivo, titulo = _esta_en_vivo(datos["handle"])
        if en_vivo:
            novedades.append(f"{datos['nombre']} está en vivo" + (f" ({titulo})" if titulo else ""))
            continue
        titulo_nuevo = _subio_hace_poco(datos["id"])
        if titulo_nuevo:
            novedades.append(f"{datos['nombre']} subió: {titulo_nuevo}")
    if not novedades:
        return "Ningún canal tiene novedades ahora."
    return "; ".join(novedades) + "."
