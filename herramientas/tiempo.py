"""Fecha y hora: el modelo local no tiene reloj ni noción de qué día es
hoy, así que "qué hora es" o "qué día es hoy" necesitan una herramienta.
Nombres de día/mes a mano en vez de locale.setlocale: es un daemon de
fondo de larga vida y setear el locale del proceso es un cambio global que
podría afectar cómo otras libs formatean números o fechas.
"""

from datetime import datetime

from herramientas import herramienta

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


@herramienta
def consultar_hora() -> str:
    """Devuelve la fecha y hora actuales."""
    ahora = datetime.now()
    dia = _DIAS[ahora.weekday()]
    mes = _MESES[ahora.month - 1]
    return f"Son las {ahora.strftime('%H:%M')} del {dia} {ahora.day} de {mes}."
