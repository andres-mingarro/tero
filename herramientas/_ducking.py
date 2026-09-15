"""Ducking: baja el volumen general de salida mientras el micrófono está
grabando (push-to-talk apretado), y lo devuelve al soltar la tecla.

Rediseñado el 2026-09-14 después de un día entero de bugs reales con el
diseño anterior (duckear cada stream de aplicación por separado, uno por
uno, identificándolos por PID en PipeWire): Spotify empaquetado como
Snap no trae su PID en el nodo que de verdad reproduce audio -- hay que
buscarlo en un nodo "cliente" hermano vía `client.id`, y sin eso Spotify
dejaba de duckearse por completo. Chrome le cambia el id (y a veces el
stream entero desaparece de `pw-dump` casi un segundo mientras carga la
página nueva) al navegar de canal por CDP, y eso dejaba el volumen
pegado en el nivel duckeado para siempre más de una vez. Cada arreglo
tapaba un caso concreto sin arreglar la clase de problema: identificar
"qué stream de PipeWire es esta aplicación, ahora mismo" es
estructuralmente fragil, cambia todo el tiempo por razones que no
tienen nada que ver con Tero.

La salida: el usuario hizo la pregunta correcta -- ¿para qué duckear por
aplicación si nunca hace falta que el volumen general y la voz de Tero
convivan al mismo tiempo? El único motivo real para duckear es proteger
la grabación del micrófono mientras está abierta. Ni "pensando"
(STT + cerebro, el micrófono ya se cerró) ni "hablando" (TTS, la propia
voz de Tero) necesitan nada duckeado -- no hay grabación en curso que
proteger, y si el usuario quiere interrumpir a Tero mientras habla,
aprieta la tecla de nuevo (barge-in, ver main.py) sin que el volumen
tenga nada que ver con eso. Acotando el duckeo a exactamente la ventana
en que la tecla está apretada (`on_down` -> `on_up`, ya no atado a
"escuchando"/"pensando"/"hablando"/"idle") alcanza con el volumen
**general del sistema** (`@DEFAULT_AUDIO_SINK@`) -- que siempre existe,
no aparece ni desaparece a mitad de una navegación como un stream de
Chrome -- y desaparece toda la complejidad de rastrear aplicaciones
individuales. La única razón por la que este módulo nunca había tocado
el sink general es que la propia voz de Tero (TTS, los beeps) sale por
ese mismo sink -- pero como el duckeo ahora dura exactamente lo que dura
la grabación, nunca se solapa con nada que Tero necesite que se escuche
fuerte: el usuario todavía tiene la tecla apretada, Tero no dijo ni una
palabra.

Un solo hilo en segundo plano interpola el volumen hacia un objetivo que
puede cambiar mientras corre, sin bloquear nunca al hilo que llama.
`wpctl get-volume` redondea a 2 decimales en su salida de texto:
releerlo en cada paso de la rampa para calcular el siguiente (en vez de
llevar la cuenta uno mismo) lo traba apenas la diferencia baja de ~0.01
-- por eso acá se lee el volumen real una sola vez por ciclo completo
(bootstrap) y de ahí en más el propio Ducker es la única fuente de
verdad de "dónde está el volumen ahora".
"""

import subprocess
import threading
import time

_SINK = "@DEFAULT_AUDIO_SINK@"

_VOLUMEN_DUCKED = 0.10
_PASO_S = 0.02
# Bajar más rápido que subir (tapar lo que esté sonando antes de que el
# mic termine de abrirse) y subir bien despacio (que no se note el
# regreso) -- mismo criterio de suavizado asimétrico que ya se usa en el
# soul-connector para el RMS.
_FACTOR_BAJADA = 0.22
_FACTOR_SUBIDA = 0.05
_UMBRAL_LISTO = 0.004


def _volumen_sink() -> float | None:
    resultado = subprocess.run(
        ["wpctl", "get-volume", _SINK], capture_output=True, text=True, check=False
    )
    if resultado.returncode != 0:
        return None
    for parte in resultado.stdout.split():
        try:
            return float(parte)
        except ValueError:
            continue
    return None


def _set_volumen_sink(valor: float) -> None:
    subprocess.run(
        ["wpctl", "set-volume", _SINK, f"{max(0.0, min(1.0, valor)):.3f}"],
        capture_output=True,
        check=False,
    )


class Ducker:
    def __init__(self):
        self._lock = threading.Lock()
        # Se capturan una sola vez por ciclo (ver nota del módulo) -- si
        # el usuario cambia el volumen a mano mientras Tero está en
        # medio de algo, el próximo ciclo lo va a pisar con este valor
        # viejo. No vale la pena resolverlo: es un asistente de uso
        # personal, y basta con soltar la tecla para que el próximo
        # apretón recapture el volumen real actual.
        self._volumen_real: float | None = None  # a qué volumen volver al soltar la tecla
        self._actual: float | None = None  # nuestra propia estimación en curso
        self._objetivo: float | None = None
        self._hilo: threading.Thread | None = None

    def activar(self) -> None:
        """Llamar justo al abrir el micrófono (on_down)."""
        with self._lock:
            self._objetivo = _VOLUMEN_DUCKED
            self._asegurar_hilo()

    def desactivar(self) -> None:
        """Llamar justo al cerrar el micrófono (on_up)."""
        with self._lock:
            if self._volumen_real is None:
                return  # nunca se llegó a activar
            self._objetivo = self._volumen_real
            self._asegurar_hilo()

    def _asegurar_hilo(self) -> None:
        if self._hilo is None or not self._hilo.is_alive():
            self._hilo = threading.Thread(target=self._rampa, daemon=True)
            self._hilo.start()

    def _rampa(self) -> None:
        with self._lock:
            actual = self._actual
        if actual is None:
            # Bootstrap: primera vez que se duckea en este ciclo -- única
            # lectura real de todo el ciclo.
            real = _volumen_sink()
            if real is None:
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
                _set_volumen_sink(actual)
                with self._lock:
                    self._actual = actual
                    if actual == self._volumen_real:
                        # Se completó una vuelta entera (bajó y volvió a
                        # subir hasta el real): se olvida el bootstrap
                        # para que el próximo apretón relea el volumen
                        # real de cero, por si el usuario lo cambió a
                        # mano mientras tanto.
                        self._actual = None
                        self._volumen_real = None
                return
            factor = _FACTOR_BAJADA if objetivo < actual else _FACTOR_SUBIDA
            actual += (objetivo - actual) * factor
            _set_volumen_sink(actual)
            with self._lock:
                self._actual = actual
            time.sleep(_PASO_S)
