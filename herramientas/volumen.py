"""Control de volumen vía wpctl (PipeWire)."""

import subprocess
from typing import Literal

from herramientas import herramienta

_SINK = "@DEFAULT_AUDIO_SINK@"


@herramienta
def ajustar_volumen(
    accion: Literal["subir", "bajar", "silenciar", "activar"], porcentaje: int = 10
) -> str:
    """Sube, baja, silencia o reactiva el volumen del sistema.

    porcentaje: cuánto subir o bajar (solo aplica a subir/bajar).
    """
    if accion == "subir":
        subprocess.run(["wpctl", "set-volume", _SINK, f"{porcentaje}%+"], check=False)
        return f"Subí el volumen {porcentaje}%."
    if accion == "bajar":
        subprocess.run(["wpctl", "set-volume", _SINK, f"{porcentaje}%-"], check=False)
        return f"Bajé el volumen {porcentaje}%."
    if accion == "silenciar":
        subprocess.run(["wpctl", "set-mute", _SINK, "1"], check=False)
        return "Silencié el volumen."
    if accion == "activar":
        subprocess.run(["wpctl", "set-mute", _SINK, "0"], check=False)
        return "Reactivé el volumen."
    return f"No entendí la acción de volumen {accion!r}."
