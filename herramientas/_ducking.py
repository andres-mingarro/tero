"""Ducking de música: baja el volumen de Spotify mientras Tero escucha,
piensa o habla, y lo devuelve al volumen real al volver a idle.

Dos enfoques descartados, probados en vivo:

- `playerctl volume` (MPRIS, mismo mecanismo que control_media): el
  cliente de Spotify para Linux no implementa SetVolume vía MPRIS -- el
  comando devuelve éxito pero no cambia nada.
- La Web API de Spotify (`/me/player/volume`, mismo mecanismo de auth que
  herramientas/musica.py): funciona, pero /me/player/devices tarda 1-3s
  en reflejar un cambio propio (eventual consistency del lado de
  Spotify), y cada paso es un request HTTP -- demasiado lento para una
  rampa que tiene que notarse ya al soltar la tecla.

Lo que sí funciona: el cliente de Spotify aplica su volumen ajustando el
volumen del propio stream de salida en PipeWire (`wpctl status` lo lista
bajo "Streams", con su propio node id, no el sink del sistema). Ajustarlo
ahí es una llamada local instantánea (~10ms), sin lag de sincronización,
y no toca el volumen del sistema (`herramientas/volumen.py`, wpctl sobre
@DEFAULT_AUDIO_SINK@): ese sink también lo usa el TTS para salir, así que
bajarlo de golpe se llevaría puesta la voz de Tero.

Un solo hilo en segundo plano interpola el volumen hacia un objetivo que
puede cambiar mientras corre (redirigir una bajada a mitad de camino si
se suelta la tecla rápido), sin bloquear nunca al hilo que llama.

Importante: `wpctl get-volume` redondea a 2 decimales en su salida de
texto. Confirmado en vivo que leer el volumen de vuelta en cada paso de
la rampa para calcular el siguiente (en vez de llevar la cuenta uno
mismo) hace que se trabe apenas la diferencia baja de ~0.01 -- el paso
calculado es más chico que la resolución de lectura, wpctl devuelve
siempre el mismo valor redondeado, y la rampa nunca converge. Por eso acá
se lee una sola vez por proceso (bootstrap, antes de tocar nada) y de ahí
en más el propio Ducker es la única fuente de verdad de "dónde está el
volumen ahora": son sus propios `_set_volumen_nodo` los que lo mueven, sin
volver a preguntarle a wpctl.
"""

import json
import subprocess
import threading
import time

_VOLUMEN_DUCKED = 0.10
_PASO_S = 0.02
# Bajar rápido (tapar la música antes de que el mic termine de abrirse) y
# subir despacio (que no se note el regreso) -- mismo criterio de
# suavizado asimétrico que ya se usa en la boca para el RMS.
_FACTOR_BAJADA = 0.35
_FACTOR_SUBIDA = 0.12
_UMBRAL_LISTO = 0.004


def _nodos_spotify() -> list[int]:
    try:
        resultado = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, check=False, timeout=2.0
        )
        nodos = json.loads(resultado.stdout)
    except Exception:
        return []
    ids = []
    for nodo in nodos:
        info = nodo.get("info") or {}
        props = info.get("props") or {}
        es_salida_de_audio = props.get("media.class") == "Stream/Output/Audio"
        if es_salida_de_audio and props.get("application.name") == "Spotify" and info.get("state"):
            ids.append(nodo["id"])
    return ids


def _volumen_nodo(id_: int) -> float | None:
    resultado = subprocess.run(
        ["wpctl", "get-volume", str(id_)], capture_output=True, text=True, check=False
    )
    if resultado.returncode != 0:
        return None
    for parte in resultado.stdout.split():
        try:
            return float(parte)
        except ValueError:
            continue
    return None


def _set_volumen_nodo(id_: int, valor: float) -> None:
    subprocess.run(
        ["wpctl", "set-volume", str(id_), f"{max(0.0, min(1.0, valor)):.3f}"],
        capture_output=True,
        check=False,
    )


class Ducker:
    def __init__(self):
        self._lock = threading.Lock()
        # Se capturan una sola vez por proceso y no se vuelven a leer (ver
        # nota del módulo) -- si el usuario cambia el volumen de Spotify a
        # mano mientras Tero está en idle entre conversaciones, el próximo
        # ciclo de ducking lo va a pisar con este valor viejo. No vale la
        # pena resolverlo: es un asistente de uso personal, y basta con
        # reiniciar Tero para que recapture el volumen real actual.
        self._volumen_real: float | None = None  # a qué volumen volver al desactivar
        self._actual: float | None = None  # nuestra propia estimación en curso
        self._objetivo: float | None = None
        self._hilo: threading.Thread | None = None

    def activar(self) -> None:
        """Llamar al entrar a un estado no-idle (escuchando/pensando/hablando)."""
        with self._lock:
            self._objetivo = _VOLUMEN_DUCKED
            self._asegurar_hilo()

    def desactivar(self) -> None:
        """Llamar al volver a idle."""
        with self._lock:
            if self._volumen_real is None:
                return  # nunca se llegó a activar (no había Spotify sonando)
            self._objetivo = self._volumen_real
            self._asegurar_hilo()

    def _asegurar_hilo(self) -> None:
        if self._hilo is None or not self._hilo.is_alive():
            self._hilo = threading.Thread(target=self._rampa, daemon=True)
            self._hilo.start()

    def _rampa(self) -> None:
        nodos = _nodos_spotify()
        if not nodos:
            with self._lock:
                self._objetivo = None
            return

        with self._lock:
            actual = self._actual
        if actual is None:
            # Bootstrap: primera vez que se duckea en este proceso -- única
            # lectura real de todo el ciclo de vida del Ducker.
            real = _volumen_nodo(nodos[0])
            if real is None:
                with self._lock:
                    self._objetivo = None
                return
            actual = real
            with self._lock:
                self._volumen_real = real
                self._actual = real

        while True:
            with self._lock:
                objetivo = self._objetivo
            if objetivo is None:
                return
            if abs(objetivo - actual) < _UMBRAL_LISTO:
                actual = objetivo
                for id_ in nodos:
                    _set_volumen_nodo(id_, actual)
                with self._lock:
                    self._actual = actual
                return
            factor = _FACTOR_BAJADA if objetivo < actual else _FACTOR_SUBIDA
            actual += (objetivo - actual) * factor
            for id_ in nodos:
                _set_volumen_nodo(id_, actual)
            with self._lock:
                self._actual = actual
            time.sleep(_PASO_S)
