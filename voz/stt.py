"""Transcripción local con faster-whisper."""

import numpy as np
from faster_whisper import WhisperModel

MUESTREO_HZ = 16000

# Sesga la transcripción hacia nombres propios que Whisper viene errando
# feo (visto en vivo: "Trelew" -> "entre el EU", "Toay" -> "todo ahí",
# "Jamiroquai" -> variantes random cada vez). initial_prompt no se
# transcribe, solo condiciona al decoder para que las reconozca si suenan
# parecido. Ciudades del usuario (Trelew, de donde es; Toay, donde vive
# ahora) van primero porque son las que más va a nombrar. Ampliar acá si
# aparecen más nombres problemáticos.
_PISTAS_NOMBRES = (
    "Trelew, Toay, La Pampa, Rawson, Santa Rosa, Puerto Madryn, "
    "Comodoro Rivadavia, Charly García, Jamiroquai, Fito Páez, "
    "Soda Estéreo, Billie Eilish."
)


class STT:
    def __init__(self, modelo: str = "small", device: str = "auto", compute_type: str = "default"):
        self._modelo = WhisperModel(modelo, device=device, compute_type=compute_type)

    def transcribir(self, audio: np.ndarray) -> str:
        """audio: mono float32 a 16kHz, en el rango [-1, 1]."""
        segmentos, _ = self._modelo.transcribe(
            audio, language="es", initial_prompt=_PISTAS_NOMBRES
        )
        return " ".join(segmento.text.strip() for segmento in segmentos).strip()
