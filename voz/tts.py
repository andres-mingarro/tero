"""Síntesis de voz local con Piper."""

from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from piper import PiperVoice

RUTA_MODELOS = Path(__file__).parent / "modelos"

_VENTANA_NIVEL_S = 0.05  # cada cuánto se reporta un nivel de audio (boca)


class TTS:
    def __init__(self, voz: str = "es_AR-daniela-high"):
        modelo = RUTA_MODELOS / f"{voz}.onnx"
        if not modelo.exists():
            raise FileNotFoundError(
                f"Falta el modelo de voz {modelo}. Descargalo con:\n"
                f"  uv run python -m piper.download_voices {voz} --download-dir {RUTA_MODELOS}"
            )
        self._voz = PiperVoice.load(modelo)

    def hablar(self, texto: str, on_nivel: Callable[[float], None] | None = None) -> None:
        """on_nivel: callback opcional, RMS (0-1 aprox) cada _VENTANA_NIVEL_S
        segundos durante la reproducción -- lo usa la boca para animarse."""
        for trozo in self._voz.synthesize(texto):
            audio = trozo.audio_float_array
            if on_nivel is None:
                sd.play(audio, samplerate=trozo.sample_rate)
                sd.wait()
            else:
                self._reproducir_con_nivel(audio, trozo.sample_rate, on_nivel)

    def _reproducir_con_nivel(
        self, audio: np.ndarray, samplerate: int, on_nivel: Callable[[float], None]
    ) -> None:
        """Reproduce con un OutputStream + callback (continuo, sin los
        micro-cortes de trocear con sd.play()/wait() repetidos) y va
        reportando el RMS de cada bloque que efectivamente suena."""
        paso = max(1, int(samplerate * _VENTANA_NIVEL_S))
        posicion = 0
        terminado = False

        def callback(outdata, frames, tiempo, status):
            nonlocal posicion, terminado
            bloque = audio[posicion : posicion + frames]
            outdata[: len(bloque), 0] = bloque
            if len(bloque) < frames:
                outdata[len(bloque) :, 0] = 0.0
                terminado = True
            rms = float(np.sqrt(np.mean(np.square(bloque)))) if len(bloque) else 0.0
            # sqrt(rms) en vez de rms*k: lineal aplastaba las partes
            # tranquilas de la voz (rms bajo) cerca de cero, se veían casi
            # sin movimiento. La raíz cuadrada las levanta relativamente
            # más, sin perder que los picos sigan llegando cerca de 1.
            on_nivel(min(1.0, (rms**0.5) * 1.7))
            posicion += frames
            if terminado:
                raise sd.CallbackStop

        with sd.OutputStream(
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            blocksize=paso,
            callback=callback,
        ) as stream:
            while stream.active:
                sd.sleep(30)
