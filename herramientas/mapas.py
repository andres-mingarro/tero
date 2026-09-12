"""Distancia y ruta entre dos lugares: geocodifica con la misma API de
Open-Meteo que ya usa clima.py (gratis, sin clave) y calcula la ruta real
con el servidor demo público de OSRM (gratis, sin clave, sin registro).
Además abre el trayecto en Google Maps para que quede visible.
"""

import urllib.parse
import webbrowser

import httpx

from herramientas import herramienta

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def _geocodificar(lugar: str) -> tuple[float, float, str] | None:
    resultado = httpx.get(
        _GEOCODING_URL, params={"name": lugar, "count": 1, "language": "es"}, timeout=5.0
    ).json()
    resultados = resultado.get("results")
    if not resultados:
        return None
    lugar_encontrado = resultados[0]
    return lugar_encontrado["latitude"], lugar_encontrado["longitude"], lugar_encontrado["name"]


@herramienta
def calcular_viaje(origen: str, destino: str) -> str:
    """Calcula distancia y tiempo de viaje en auto ENTRE DOS lugares
    distintos, y abre la ruta en Google Maps. Es solo para trayectos: si
    el usuario solo quiere ver dónde queda un único lugar (sin origen ni
    destino separados), no uses esto -- usá buscar_en_sitio con
    sitio="maps" en su lugar.

    origen y destino: nombres de ciudades o lugares, ej. "Trelew", "Toay".
    """
    geo_origen = _geocodificar(origen)
    if geo_origen is None:
        return f"La herramienta 'calcular_viaje' falló: no encontré {origen!r}."
    geo_destino = _geocodificar(destino)
    if geo_destino is None:
        return f"La herramienta 'calcular_viaje' falló: no encontré {destino!r}."

    lat1, lon1, nombre1 = geo_origen
    lat2, lon2, nombre2 = geo_destino

    ruta = httpx.get(
        f"{_OSRM_URL}/{lon1},{lat1};{lon2},{lat2}",
        params={"overview": "false"},
        timeout=10.0,
    ).json()
    if ruta.get("code") != "Ok":
        return f"La herramienta 'calcular_viaje' falló: no pude calcular la ruta entre {nombre1!r} y {nombre2!r}."

    distancia_km = ruta["routes"][0]["distance"] / 1000
    duracion_h = ruta["routes"][0]["duration"] / 3600

    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={urllib.parse.quote(origen)}&destination={urllib.parse.quote(destino)}"
    )
    webbrowser.open(url)

    return (
        f"De {nombre1} a {nombre2} hay {distancia_km:.0f} km, unas {duracion_h:.1f} horas en auto. "
        f"Ya abrí la ruta en Google Maps. URL: {url}"
    )
