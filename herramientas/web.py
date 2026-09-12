"""Abrir URLs y buscar en sitios conocidos: el modelo arma la URL, no hay
agente de navegador."""

import re
import urllib.parse
import webbrowser
from typing import Literal

from herramientas import herramienta


@herramienta
def abrir_url(url: str) -> str:
    """Abre una URL en el navegador por defecto."""
    webbrowser.open(url)
    return f"Abrí {url}."


def _slug(texto: str) -> str:
    texto = texto.lower().strip()
    texto = re.sub(r"[^a-z0-9áéíóúñ ]", "", texto)
    return re.sub(r"\s+", "-", texto)


@herramienta
def buscar_en_sitio(
    sitio: Literal["mercadolibre", "google", "youtube", "amazon", "maps"], consulta: str
) -> str:
    """Busca `consulta` en el sitio indicado y abre el resultado en el navegador."""
    if sitio == "mercadolibre":
        url = f"https://listado.mercadolibre.com.ar/{urllib.parse.quote(_slug(consulta))}"
    elif sitio == "youtube":
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(consulta)}"
    elif sitio == "amazon":
        url = f"https://www.amazon.com/s?k={urllib.parse.quote(consulta)}"
    elif sitio == "maps":
        url = f"https://www.google.com/maps?q={urllib.parse.quote(consulta)}"
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(consulta)}"
    webbrowser.open(url)
    # El link real va en el resultado a propósito: si el usuario después
    # pide "mandalo al celular" o "pasame el link", el modelo tiene el URL
    # de verdad para copiar en vez de inventar una dirección de memoria.
    return f"Abrí la búsqueda de {consulta!r} en {sitio}. URL: {url}"
