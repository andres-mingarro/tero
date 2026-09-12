"""Síntesis de voz local con Piper."""

from pathlib import Path

import sounddevice as sd
from piper import PiperVoice

RUTA_MODELOS = Path(__file__).parent / "modelos"


class TTS:
    def __init__(self, voz: str = "es_AR-daniela-high"):
        modelo = RUTA_MODELOS / f"{voz}.onnx"
        if not modelo.exists():
            raise FileNotFoundError(
                f"Falta el modelo de voz {modelo}. Descargalo con:\n"
                f"  uv run python -m piper.download_voices {voz} --download-dir {RUTA_MODELOS}"
            )
        self._voz = PiperVoice.load(modelo)

    def hablar(self, texto: str) -> None:
        for trozo in self._voz.synthesize(texto):
            sd.play(trozo.audio_float_array, samplerate=trozo.sample_rate)
            sd.wait()
