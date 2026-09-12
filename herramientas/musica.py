"""Música: reproducción real vía la Web API de Spotify (ver
herramientas/_spotify_auth.py para el login OAuth). No alcanza con abrir
una URL de búsqueda — eso solo muestra resultados, no elige ni arranca una
canción — así que se resuelve la búsqueda contra /v1/search para conseguir
el track exacto y se manda a reproducir con /v1/me/player/play.

Requiere Spotify Premium: el endpoint de reproducción de la Web API
devuelve 403 en cuentas free.
"""

import random
import subprocess
import time
from typing import Literal

import httpx

from herramientas import _spotify_auth, herramienta

_API = "https://api.spotify.com/v1"


def _headers() -> dict:
    return {"Authorization": f"Bearer {_spotify_auth.obtener_token()}"}


def _buscar_track(consulta: str) -> dict | None:
    respuesta = httpx.get(
        f"{_API}/search",
        params={"q": consulta, "type": "track", "limit": 1},
        headers=_headers(),
        timeout=10.0,
    )
    respuesta.raise_for_status()
    items = respuesta.json()["tracks"]["items"]
    return items[0] if items else None


def _dispositivo_activo() -> str | None:
    respuesta = httpx.get(f"{_API}/me/player/devices", headers=_headers(), timeout=10.0)
    respuesta.raise_for_status()
    dispositivos = respuesta.json()["devices"]
    if not dispositivos:
        return None
    activo = next((d for d in dispositivos if d["is_active"]), dispositivos[0])
    return activo["id"]


def estado_reproduccion() -> dict | None:
    """Qué suena en Spotify ahora (texto, progreso y duración en ms), o
    None si no hay nada cargado. Si está en pausa, sigue devolviendo el
    dict (con reproduciendo=False) en vez de None -- la boca lo muestra
    distinto (en rojo, sin avanzar el progreso) en vez de ocultar todo
    como si no hubiera nada. No es una herramienta del modelo -- la usa
    la boca para mostrar debajo de la onda qué está sonando."""
    try:
        respuesta = httpx.get(f"{_API}/me/player/currently-playing", headers=_headers(), timeout=5.0)
        if respuesta.status_code != 200 or not respuesta.content:
            return None
        datos = respuesta.json()
        item = datos.get("item")
        if not item:
            return None
        artista = item["artists"][0]["name"] if item.get("artists") else "?"
        return {
            "texto": f"{item['name']} · {artista}",
            "progreso_ms": datos.get("progress_ms") or 0,
            "duracion_ms": item.get("duration_ms") or 0,
            "reproduciendo": bool(datos.get("is_playing")),
        }
    except Exception:
        return None


def _asegurar_dispositivo() -> str | None:
    """Dispositivo activo de Spotify Connect, abriendo la app y
    reintentando con backoff si no hay ninguno visible todavía."""
    device_id = _dispositivo_activo()
    if device_id is None:
        subprocess.run(["xdg-open", "spotify:"], check=False)
        for espera in (2, 2, 3, 3):
            time.sleep(espera)
            device_id = _dispositivo_activo()
            if device_id is not None:
                break
    return device_id


def _reproducir_uris(uris: list[str], device_id: str) -> None:
    # Una lista, no una sola uri: con una sola canción sin cola detrás,
    # "siguiente" no tiene a dónde avanzar (probado en vivo: control_media
    # con accion "siguiente" no hacía nada porque no había próximo tema).
    respuesta = httpx.put(
        f"{_API}/me/player/play",
        params={"device_id": device_id},
        headers=_headers(),
        json={"uris": uris},
        timeout=10.0,
    )
    respuesta.raise_for_status()


def _favoritos_aleatorios(cantidad: int) -> list[dict]:
    """Hasta `cantidad` tracks de "Tus me gusta", de una ventana al azar,
    ya mezclados. Lista vacía si no hay favoritos o falla la lectura."""
    try:
        respuesta = httpx.get(
            f"{_API}/me/tracks", params={"limit": 1}, headers=_headers(), timeout=10.0
        )
        respuesta.raise_for_status()
        total = respuesta.json().get("total", 0)
        if total == 0:
            return []
        tamano = min(cantidad, total)
        offset = random.randint(0, max(0, total - tamano))
        respuesta = httpx.get(
            f"{_API}/me/tracks",
            params={"limit": tamano, "offset": offset},
            headers=_headers(),
            timeout=10.0,
        )
        respuesta.raise_for_status()
        tracks = [item["track"] for item in respuesta.json()["items"]]
        random.shuffle(tracks)
        return tracks
    except Exception:
        return []


@herramienta
def reproducir_musica(busqueda: str) -> str:
    """Busca una canción, álbum o artista y lo reproduce en Spotify.

    busqueda: texto libre, ej. "Metallica black album" o "Bad Bunny".
    """
    track = _buscar_track(busqueda)
    if track is None:
        return f"La herramienta 'reproducir_musica' falló: no encontré ninguna canción para {busqueda!r} en Spotify."

    device_id = _asegurar_dispositivo()
    if device_id is None:
        return "La herramienta 'reproducir_musica' falló: no hay ningún dispositivo de Spotify activo, ni abriendo la app."

    # Después de esta canción, se encolan algunos favoritos al azar --
    # sin esto, "siguiente" no tenía a dónde avanzar (una sola canción
    # suelta no es una cola real).
    cola = [track["uri"]] + [
        t["uri"] for t in _favoritos_aleatorios(10) if t["uri"] != track["uri"]
    ]
    _reproducir_uris(cola, device_id)
    artista = track["artists"][0]["name"] if track["artists"] else "?"
    return f"Reproduciendo {track['name']!r} de {artista}."


@herramienta
def reproducir_musica_aleatoria() -> str:
    """Elige varias canciones al azar de "Tus me gusta" (favoritos) del
    usuario en Spotify y las pone en cola, empezando por una de ellas.
    Usar para pedidos genéricos de música que NO nombran artista/canción/
    álbum (ej. "poné música", "poné algo", "poné alguna canción") -- no
    repite siempre lo último que sonó, y "siguiente" tiene a dónde ir."""
    tracks = _favoritos_aleatorios(15)
    if not tracks:
        return "La herramienta 'reproducir_musica_aleatoria' falló: no tenés canciones en 'Tus me gusta' en Spotify."

    device_id = _asegurar_dispositivo()
    if device_id is None:
        return "La herramienta 'reproducir_musica_aleatoria' falló: no hay ningún dispositivo de Spotify activo, ni abriendo la app."

    _reproducir_uris([t["uri"] for t in tracks], device_id)
    primero = tracks[0]
    artista = primero["artists"][0]["name"] if primero["artists"] else "?"
    return f"Reproduciendo {primero['name']!r} de {artista} (de tus favoritos)."


_COMANDOS_PLAYERCTL = {
    "reproducir": "play",
    "pausar": "pause",
    "siguiente": "next",
    "anterior": "previous",
}


def _playerctl(accion: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["playerctl", _COMANDOS_PLAYERCTL[accion]], capture_output=True, text=True, check=False
    )


@herramienta
def control_media(accion: Literal["reproducir", "pausar", "siguiente", "anterior"]) -> str:
    """Controla la reproducción multimedia actual (play, pausa, siguiente, anterior).

    Vía MPRIS (playerctl): funciona con Spotify, navegadores y la mayoría
    de reproductores modernos en Linux, sin importar cuál esté sonando.
    Requiere `playerctl` instalado.
    """
    resultado = _playerctl(accion)
    if resultado.returncode != 0 and accion == "reproducir":
        # "No player could handle this command": no hay ningún reproductor
        # con sesión MPRIS activa, probablemente porque Spotify ni está
        # abierto. Se abre (igual que en reproducir_musica) y se reintenta.
        subprocess.run(["xdg-open", "spotify:"], check=False)
        for espera in (2, 2, 3, 3):
            time.sleep(espera)
            resultado = _playerctl(accion)
            if resultado.returncode == 0:
                break
    if resultado.returncode != 0:
        return f"La herramienta 'control_media' falló: {resultado.stderr.strip() or 'sin reproductor activo'}."
    return f"Listo, {accion}."
