"""Ducking: baja el volumen de **cualquier cosa que esté sonando** en el
sistema mientras Tero escucha, piensa o habla, y lo devuelve al volumen
real al volver a idle. Regla global a propósito, no una lista de
aplicaciones conocidas (Spotify, la ventana de YouTube) -- un pedido
explícito del usuario tras ver que YouTube tapaba su voz igual que
Spotify, con el mismo problema de fondo: cualquier audio a volumen normal
le gana al micrófono y arruina el STT, sea cual sea la app. Se duckea
todo salvo la salida del propio Tero (TTS/beeps), identificada por PID.

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
se lee una sola vez **por ciclo completo** (bootstrap al primer `activar()`
después de haber vuelto a idle, nunca en medio de una rampa) y de ahí en
más el propio Ducker es la única fuente de verdad de "dónde está el
volumen ahora": son sus propios `_set_volumen_nodo` los que lo mueven, sin
volver a preguntarle a wpctl. El bootstrap se repite en cada ciclo (no
solo la primera vez del proceso) para no arrastrar el volumen real de una
app vieja a una app distinta que empezó a sonar después.
"""

import json
import os
import subprocess
import threading
import time

_VOLUMEN_DUCKED = 0.10
_PASO_S = 0.02
# Bajar más rápido que subir (tapar la música antes de que el mic termine
# de abrirse) y subir bien despacio (que no se note el regreso) -- mismo
# criterio de suavizado asimétrico que ya se usa en el soul-connector
# para el RMS. Factores bajados el 2026-09-14 (0.35/0.12 originales
# sonaban a corte seco, "muy pronunciado" según el usuario) -- 0.22 sigue
# terminando la bajada en well under medio segundo, así que no vuelve a
# abrir la ventana de audio sucio en el mic que motivó el ducking global
# (ver encabezado del módulo); 0.05 hace un regreso bien gradual, de un
# par de segundos, que ya no tiene esa restricción de tiempo.
_FACTOR_BAJADA = 0.22
_FACTOR_SUBIDA = 0.05
_UMBRAL_LISTO = 0.004


def _nodos_a_duckear() -> list[int]:
    """Todo stream de salida de audio realmente sonando (`state=="running"`)
    ahora mismo, de cualquier aplicación, salvo el del propio proceso de
    Tero (el TTS y los beeps también son streams de PipeWire, y duckearse
    a sí mismo cortaría la propia voz)."""
    try:
        resultado = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, check=False, timeout=2.0
        )
        nodos = json.loads(resultado.stdout)
    except Exception:
        return []
    propio_pid = os.getpid()
    ids = []
    for nodo in nodos:
        info = nodo.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio" or info.get("state") != "running":
            continue
        pid = props.get("application.process.id")
        if pid is not None and int(pid) == propio_pid:
            continue
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
                return  # nunca se llegó a activar (no había nada sonando)
            self._objetivo = self._volumen_real
            self._asegurar_hilo()

    def _asegurar_hilo(self) -> None:
        if self._hilo is None or not self._hilo.is_alive():
            self._hilo = threading.Thread(target=self._rampa, daemon=True)
            self._hilo.start()

    def _rampa(self) -> None:
        # Si Spotify y YouTube suenan a la vez con volúmenes reales
        # distintos, esto los empareja al del primer nodo encontrado en
        # vez de llevar una rampa independiente por nodo -- caso raro (lo
        # normal es que suene una sola cosa a la vez) y no vale la pena
        # la complejidad de trackear varias rampas en paralelo por ahora.
        nodos = _nodos_a_duckear()
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
                    if actual == self._volumen_real:
                        # Se completó una vuelta entera (bajó y volvió a
                        # subir hasta el real): se olvida el bootstrap
                        # para que la próxima activación relea el volumen
                        # real de cero. Con ducking global (no solo
                        # Spotify) esto importa más que antes -- entre
                        # una conversación y la siguiente puede haber
                        # arrancado una app nueva con su propio volumen,
                        # y sin este reset se le aplicaría el valor viejo
                        # de otra cosa en vez del suyo.
                        self._actual = None
                        self._volumen_real = None
                return
            factor = _FACTOR_BAJADA if objetivo < actual else _FACTOR_SUBIDA
            actual += (objetivo - actual) * factor
            for id_ in nodos:
                _set_volumen_nodo(id_, actual)
            with self._lock:
                self._actual = actual
            time.sleep(_PASO_S)
