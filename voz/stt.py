"""Transcripción local con faster-whisper."""

import numpy as np
from faster_whisper import WhisperModel

MUESTREO_HZ = 16000


class STT:
    def __init__(self, modelo: str = "small", device: str = "auto", compute_type: str = "default"):
        self._modelo = WhisperModel(modelo, device=device, compute_type=compute_type)

    def transcribir(self, audio: np.ndarray) -> str:
        """audio: mono float32 a 16kHz, en el rango [-1, 1]."""
        segmentos, _ = self._modelo.transcribe(audio, language="es")
        return " ".join(segmento.text.strip() for segmento in segmentos).strip()
