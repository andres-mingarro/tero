"""Abrir URLs: el modelo arma la URL, no hay agente de navegador."""

import webbrowser

from herramientas import herramienta


@herramienta
def abrir_url(url: str) -> str:
    """Abre una URL en el navegador por defecto."""
    webbrowser.open(url)
    return f"Abrí {url}."
