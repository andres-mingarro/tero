"""Mandar cosas al celular vía un bot de Telegram propio (ver
herramientas/_telegram.py). Solo texto/links por ahora -- si hace falta
mandar fotos o archivos más adelante, la API de Telegram también tiene
sendPhoto/sendDocument, se agrega como otra herramienta cuando haga falta
de verdad, no antes.
"""

import httpx

from herramientas import _telegram, herramienta

_API = "https://api.telegram.org"


@herramienta
def mandar_al_celular(texto: str) -> str:
    """Manda un mensaje de texto (o un link) al celular del usuario por Telegram.

    texto: lo que se quiere mandar, ej. una dirección, un link, una nota.
    """
    respuesta = httpx.post(
        f"{_API}/bot{_telegram.token()}/sendMessage",
        json={"chat_id": _telegram.chat_id(), "text": texto},
        timeout=10.0,
    )
    if respuesta.status_code != 200:
        return f"La herramienta 'mandar_al_celular' falló: {respuesta.text}"
    return "Listo, te lo mandé al celular."
