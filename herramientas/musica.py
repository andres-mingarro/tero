"""Música: reproducción real vía la Web API de Spotify (ver
herramientas/_spotify_auth.py para el login OAuth). No alcanza con abrir
una URL de búsqueda — eso solo muestra resultados, no elige ni arranca una
canción — así que se resuelve la búsqueda contra /v1/search para conseguir
el track exacto y se manda a reproducir con /v1/me/player/play.

Requiere Spotify Premium: el endpoint de reproducción de la Web API
devuelve 403 en cuentas free.
"""

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
    None si no hay nada. No es una herramienta del modelo -- la usa la
    boca para mostrar debajo de la onda qué está sonando y el progreso."""
    try:
        respuesta = httpx.get(f"{_API}/me/player/currently-playing", headers=_headers(), timeout=5.0)
        if respuesta.status_code != 200 or not respuesta.content:
            return None
        datos = respuesta.json()
        item = datos.get("item")
        if not datos.get("is_playing") or not item:
            return None
        artista = item["artists"][0]["name"] if item.get("artists") else "?"
        return {
            "texto": f"{item['name']} · {artista}",
            "progreso_ms": datos.get("progress_ms") or 0,
            "duracion_ms": item.get("duration_ms") or 0,
        }
    except Exception:
        return None


@herramienta
def reproducir_musica(busqueda: str) -> str:
    """Busca una canción, álbum o artista y lo reproduce en Spotify.

    busqueda: texto libre, ej. "Metallica black album" o "Bad Bunny".
    """
    track = _buscar_track(busqueda)
    if track is None:
        return f"La herramienta 'reproducir_musica' falló: no encontré ninguna canción para {busqueda!r} en Spotify."

    device_id = _dispositivo_activo()
    if device_id is None:
        # Sin ningún dispositivo de Spotify Connect visible: probablemente
        # la app no está abierta (o recién se abrió y todavía no terminó
        # de registrarse como dispositivo — eso tarda unos segundos, más la
        # primera vez). Se abre y se reintenta con backoff en vez de una
        # sola espera fija.
        subprocess.run(["xdg-open", "spotify:"], check=False)
        for espera in (2, 2, 3, 3):
            time.sleep(espera)
            device_id = _dispositivo_activo()
            if device_id is not None:
                break
    if device_id is None:
        return "La herramienta 'reproducir_musica' falló: no hay ningún dispositivo de Spotify activo, ni abriendo la app."

    respuesta = httpx.put(
        f"{_API}/me/player/play",
        params={"device_id": device_id},
        headers=_headers(),
        json={"uris": [track["uri"]]},
        timeout=10.0,
    )
    respuesta.raise_for_status()
    artista = track["artists"][0]["name"] if track["artists"] else "?"
    return f"Reproduciendo {track['name']!r} de {artista}."


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
