"""Transcripción: Groq online primero, Whisper local de respaldo.

Por qué está armado así: lo que más le importa al usuario no es cuánta
cultura general tiene el modelo, es que Tero entienda frases libres
("abrí Spotify y poné Fito", no comandos fijos) -- y eso depende de
escuchar bien, no de razonar bien. Un pedido mal transcripto no le da
ninguna chance al cerebro (visto en vivo: "Poné música" -> "Buena
música." hizo fallar el turno entero).

Groq ofrece el mismo Whisper large-v3 gratis, sin tarjeta: en el uso de
un push-to-talk personal, el límite real del plan gratis (2000
pedidos/día) queda lejísimos. Por eso Whisper local es solo el respaldo
para cuando se corta Internet o Groq falla -- se carga bajo demanda, no
al arrancar, así los ~3,7GB de VRAM que se lleva quedan libres para que
el modelo de lenguaje entre entero en la GPU (ver cerebro/router.py).
"""

import io
import time
import wave
from pathlib import Path

import httpx
import numpy as np
from faster_whisper import WhisperModel

MUESTREO_HZ = 16000

_RUTA_KEY = Path.home() / ".config" / "tero" / "groq_key"

# Sesga la transcripción hacia nombres propios que Whisper viene errando
# feo (visto en vivo: "Trelew" -> "entre el EU", "Toay" -> "todo ahí",
# "Jamiroquai" -> variantes random cada vez). initial_prompt no se
# transcribe, solo condiciona al decoder para que las reconozca si suenan
# parecido. Ciudades del usuario (Trelew, de donde es; Toay, donde vive
# ahora) van primero porque son las que más va a nombrar. Ampliar acá si
# aparecen más nombres problemáticos. Se usa tanto local como con Groq.
_PISTAS_NOMBRES = (
    "Trelew, Toay, La Pampa, Rawson, Santa Rosa, Puerto Madryn, "
    "Comodoro Rivadavia, Charly García, Jamiroquai, Fito Páez, "
    "Soda Estéreo, Billie Eilish."
)


def groq_configurado() -> bool:
    """True si hay una API key de Groq guardada (aunque después falle)."""
    return _leer_key() is not None


def _leer_key() -> str | None:
    try:
        key = _RUTA_KEY.read_text().strip()
    except FileNotFoundError:
        return None
    return key or None


def _wav_bytes(audio: np.ndarray, muestreo_hz: int) -> bytes:
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(muestreo_hz)
        wav.writeframes(pcm16.tobytes())
    return buffer.getvalue()


class ErrorGroq(Exception):
    """Groq no transcribió: sin key, sin red, límite alcanzado o error del servidor.

    reintentar_en_s: cuánto esperar antes de volver a probar Groq, si el
    servidor lo dijo explícitamente (header Retry-After de un 429).
    """

    def __init__(self, motivo: str, reintentar_en_s: float | None = None):
        super().__init__(motivo)
        self.reintentar_en_s = reintentar_en_s


class LocalSTT:
    """Whisper corriendo en esta máquina, vía faster-whisper."""

    def __init__(self, modelo: str = "large-v3", device: str = "auto", compute_type: str = "default"):
        self._modelo = WhisperModel(modelo, device=device, compute_type=compute_type)

    def transcribir(self, audio: np.ndarray) -> str:
        """audio: mono float32 a 16kHz, en el rango [-1, 1]."""
        segmentos, _ = self._modelo.transcribe(
            audio, language="es", initial_prompt=_PISTAS_NOMBRES
        )
        return " ".join(segmento.text.strip() for segmento in segmentos).strip()


class GroqSTT:
    """Whisper corriendo en la nube de Groq (plan gratis, ver módulo)."""

    _URL = "https://api.groq.com/openai/v1/audio/transcriptions"
    # No dejar que un Groq lento retrase mucho antes de caer al respaldo
    # local: mejor un fallback un poco ansioso que una espera larga colgada.
    _TIMEOUT_S = 8.0

    def __init__(self, modelo: str = "whisper-large-v3"):
        self._modelo = modelo

    def transcribir(self, audio: np.ndarray, muestreo_hz: int) -> str:
        key = _leer_key()
        if key is None:
            raise ErrorGroq(f"sin API key ({_RUTA_KEY})")

        try:
            respuesta = httpx.post(
                self._URL,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", _wav_bytes(audio, muestreo_hz), "audio/wav")},
                data={"model": self._modelo, "language": "es", "prompt": _PISTAS_NOMBRES},
                timeout=self._TIMEOUT_S,
            )
        except httpx.RequestError as error:
            raise ErrorGroq(f"sin conexión: {error}") from error

        if respuesta.status_code == 429:
            espera = respuesta.headers.get("retry-after")
            raise ErrorGroq(
                "límite del plan gratis alcanzado",
                reintentar_en_s=float(espera) if espera else None,
            )
        if respuesta.status_code != 200:
            raise ErrorGroq(f"Groq respondió {respuesta.status_code}: {respuesta.text[:200]}")

        return (respuesta.json().get("text") or "").strip()


# Cuánto esperar antes de reintentar Groq después de una falla sin
# Retry-After explícito (sin red, error del servidor). No tan corto como
# para reintentar en cada pedido con Internet caído, ni tan largo como
# para tardar en notar que volvió.
_COOLDOWN_FALLA_S = 20.0
# Tope por si un 429 viniera con un Retry-After absurdo.
_COOLDOWN_MAX_S = 300.0


class STTHibrido:
    """Prueba Groq en cada pedido; si falla, cae a Whisper local (cargado
    recién la primera vez que hace falta) hasta que Groq vuelva a andar.

    on_aviso(texto): se llama para que el llamador lo diga en voz alta,
        al cruzar de online a offline y de vuelta -- nunca en cada
        pedido, solo en el cambio de modo.
    on_carga(texto | None, progreso): mismo protocolo que usa main.py al
        arrancar, para que el soul-connector muestre "Cargando transcripción..."
        mientras se carga el modelo local por primera vez.
    """

    def __init__(self, config_local: dict, on_aviso, on_carga, muestreo_hz: int = MUESTREO_HZ):
        self._config_local = config_local
        self._on_aviso = on_aviso
        self._on_carga = on_carga
        self._muestreo_hz = muestreo_hz
        self._groq = GroqSTT()
        self._local: LocalSTT | None = None
        self._offline = False
        self._proximo_intento_online = 0.0

    def transcribir(self, audio: np.ndarray) -> str:
        if time.monotonic() >= self._proximo_intento_online:
            try:
                texto = self._groq.transcribir(audio, self._muestreo_hz)
            except ErrorGroq as error:
                self._a_offline(error)
            else:
                self._a_online()
                return texto
        return self._transcribir_local(audio)

    def _a_offline(self, error: ErrorGroq) -> None:
        cooldown = min(error.reintentar_en_s or _COOLDOWN_FALLA_S, _COOLDOWN_MAX_S)
        self._proximo_intento_online = time.monotonic() + cooldown
        if not self._offline:
            self._offline = True
            print(f"(Groq no disponible: {error} -- paso a Whisper local)")
            self._on_aviso("Pasando a modo offline, esperá que cargo Whisper.")

    def _a_online(self) -> None:
        if self._offline:
            self._offline = False
            # Sin esto el modelo local se queda cargado en VRAM para
            # siempre después del primer corte, aunque no se vuelva a
            # usar -- justo lo que este diseño quiere evitar.
            self._local = None
            print("(Groq disponible de nuevo -- descargo Whisper local)")
            self._on_aviso("Volví a modo online.")

    def _transcribir_local(self, audio: np.ndarray) -> str:
        if self._local is None:
            self._on_carga("Cargando transcripción (modo offline)", 0.0)
            self._local = LocalSTT(**self._config_local)
            self._on_carga(None)
        return self._local.transcribir(audio)
