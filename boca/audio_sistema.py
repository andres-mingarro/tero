"""Escucha el audio del sistema (lo que sale por el sink activo, vía su
monitor de PipeWire) para que la boca reaccione a lo que está sonando de
verdad en la compu -- música de Spotify, un video, lo que sea -- no solo
cuando Tero habla.

El sink default puede cambiar (el usuario cambia de auriculares a HDMI,
etc.), así que se re-detecta cada vez que el stream se cae en vez de
asumir uno fijo.
"""

import subprocess
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

_VENTANA_S = 0.05
_UMBRAL_SILENCIO = 0.003  # por debajo de esto se considera "no está sonando nada"


def _nombre_sink_default() -> str | None:
    resultado = subprocess.run(
        ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, check=False
    )
    if resultado.returncode != 0:
        return None
    for linea in resultado.stdout.splitlines():
        if "node.name" in linea:
            return linea.split("=", 1)[1].strip().strip('"')
    return None


def _indice_monitor(nombre_sink: str) -> int | None:
    objetivo = f"{nombre_sink}.monitor"
    for i, dispositivo in enumerate(sd.query_devices()):
        if dispositivo["name"] == objetivo and dispositivo["max_input_channels"] > 0:
            return i
    return None


class MonitorAudioSistema:
    """Corre en un hilo aparte. Llama a on_nivel(rms) mientras hay señal,
    y a on_silencio() cuando no hay nada sonando (para que la boca sepa
    distinguir "no hay música" de "hay música pero está en un pasaje
    tranquilo")."""

    def __init__(self, on_nivel: Callable[[float], None], on_silencio: Callable[[], None]):
        self._on_nivel = on_nivel
        self._on_silencio = on_silencio
        self._detener = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()

    def _correr(self) -> None:
        while not self._detener.is_set():
            sink = _nombre_sink_default()
            indice = _indice_monitor(sink) if sink else None
            if indice is None:
                self._on_silencio()
                self._detener.wait(3)
                continue
            try:
                with sd.InputStream(
                    device=indice,
                    channels=2,
                    samplerate=44100,
                    blocksize=int(44100 * _VENTANA_S),
                    dtype="float32",
                    callback=self._callback,
                ):
                    while not self._detener.is_set():
                        if _nombre_sink_default() != sink:
                            break  # cambió el sink default, reabrir con el nuevo
                        sd.sleep(500)
            except Exception:
                self._on_silencio()
                self._detener.wait(3)

    def _callback(self, indata, frames, tiempo, status) -> None:
        rms = float(np.sqrt(np.mean(np.square(indata))))
        if rms < _UMBRAL_SILENCIO:
            self._on_silencio()
        else:
            self._on_nivel(min(1.0, (rms**0.5) * 2.6))
