"""Fase 1 (esqueleto): tecla -> grabación -> transcripción -> repetir por voz.

Sirve para medir la latencia real del camino local antes de sumar el
cerebro (Ollama) y las herramientas.
"""

import glob
import os
import sys
import sysconfig


def _asegurar_libs_cuda() -> None:
    """Los paquetes pip nvidia-cublas-cu12/nvidia-cudnn-cu12 traen las .so
    pero ctranslate2 las busca vía LD_LIBRARY_PATH, que solo se lee al
    arrancar el proceso. Si no está seteado, se re-ejecuta el propio
    proceso una vez con el path corregido (evita tener que exportarlo a
    mano antes de cada arranque del daemon)."""
    if os.environ.get("_TERO_CUDA_LIBS_OK"):
        return
    libs = sorted(glob.glob(os.path.join(sysconfig.get_paths()["purelib"], "nvidia", "*", "lib")))
    if not libs or all(lib in os.environ.get("LD_LIBRARY_PATH", "") for lib in libs):
        os.environ["_TERO_CUDA_LIBS_OK"] = "1"
        return
    actual = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(libs) + (":" + actual if actual else "")
    os.environ["_TERO_CUDA_LIBS_OK"] = "1"
    # execv no hereda el flag -u: sin esto, stdout queda bufferizado en
    # bloque (no es una tty) y los prints del daemon no se ven en vivo.
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


_asegurar_libs_cuda()

import time
import tomllib
from pathlib import Path

import numpy as np
import sounddevice as sd

from boca.audio_sistema import MonitorAudioSistema
from boca.server import ServidorBoca
from cerebro.router import Cerebro
from herramientas import musica
from plataforma import crear_plataforma
from voz.stt import STT
from voz.tts import TTS

RUTA_CONFIG = Path(__file__).parent / "config.toml"


def _beep(frecuencia_hz: float, duracion_s: float = 0.08) -> None:
    t = np.linspace(0, duracion_s, int(44100 * duracion_s), endpoint=False)
    tono = 0.2 * np.sin(2 * np.pi * frecuencia_hz * t).astype(np.float32)
    sd.play(tono, samplerate=44100)
    sd.wait()


class Grabador:
    """Acumula audio de un InputStream mientras está activo."""

    def __init__(self, muestreo_hz: int, canales: int, on_nivel=None):
        self._muestreo_hz = muestreo_hz
        self._canales = canales
        self._trozos: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        # Opcional: nivel del mic en vivo mientras graba, para que la boca
        # se mueva con la voz real del usuario en vez de un valor fijo.
        self._on_nivel = on_nivel
        # Auto-gain en vez de un multiplicador fijo: un número calibrado a
        # mano (ej. rms*2.5) queda bien con un mic y mal con otro -- el rms
        # real de un mic vive en una escala mucho más baja e impredecible
        # que la del audio de TTS (que sale normalizado). Se seguí el pico
        # de volumen reciente y se normaliza contra eso, así que hablar a
        # volumen normal siempre abre la onda casi al máximo, sea cual sea
        # el mic. El pico decae lento (no de golpe en un silencio corto
        # entre palabras) pero sube al instante si se habla más fuerte.
        self._pico_rms = 0.02

    def _callback(self, indata, frames, tiempo, status):
        self._trozos.append(indata.copy())
        if self._on_nivel is not None:
            rms = float(np.sqrt(np.mean(np.square(indata))))
            self._pico_rms = max(rms, self._pico_rms * 0.999)
            self._on_nivel(min(1.0, rms / self._pico_rms) ** 0.5)

    def iniciar(self) -> None:
        self._trozos = []
        self._stream = sd.InputStream(
            samplerate=self._muestreo_hz,
            channels=self._canales,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def detener(self) -> np.ndarray:
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._trozos:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._trozos, axis=0).reshape(-1)


class Tero:
    def __init__(self, config: dict):
        self._config = config
        self._plataforma = crear_plataforma(tecla=config["tecla"]["nombre"])
        self._grabador = Grabador(
            config["audio"]["muestreo_hz"], config["audio"]["canales"], on_nivel=self._nivel_boca
        )
        print("Cargando modelo de transcripción...")
        self._stt = STT(**config["stt"])
        print("Cargando voz...")
        self._tts = TTS(**config["tts"])
        self._cerebro = Cerebro(**config["cerebro"])
        self._boca = self._crear_boca()
        self._estado_voz = "idle"
        self._monitor_audio = self._crear_monitor_audio()

        self._grabando = False
        self._modo_toggle = False
        self._tiempo_down = 0.0
        self._umbral_toggle_s = config["tecla"]["umbral_toggle_s"]

        self._ultimo_poll_cancion = 0.0
        # Cuándo empezó la pausa actual (None si no está pausado o no hay
        # nada cargado). Sirve para ocultar el reproductor de la boca si
        # queda pausado mucho tiempo -- mostrar "pausado" para siempre
        # después de que el usuario se olvidó de la música es ruido visual
        # que no aporta nada.
        self._pausado_desde: float | None = None

    def _crear_boca(self) -> ServidorBoca | None:
        # La boca es un cliente opcional: si esto falla por lo que sea, el
        # daemon tiene que seguir funcionando igual, sin ventana.
        try:
            return ServidorBoca()
        except Exception as error:
            print(f"(boca no disponible: {error})")
            return None

    def _crear_monitor_audio(self) -> MonitorAudioSistema | None:
        # Igual que la boca: opcional, el daemon tiene que andar sin esto.
        try:
            return MonitorAudioSistema(
                on_nivel=self._nivel_musica_boca, on_silencio=self._silencio_musica_boca
            )
        except Exception as error:
            print(f"(monitor de audio del sistema no disponible: {error})")
            return None

    def _estado_boca(self, nombre: str) -> None:
        self._estado_voz = nombre
        if self._boca is not None:
            self._boca.estado(nombre)

    def _nivel_musica_boca(self, nivel: float) -> None:
        # Solo si Tero no está en medio de escuchar/pensar/hablar -- eso
        # siempre tiene prioridad visual sobre "hay música sonando".
        if self._boca is not None and self._estado_voz == "idle":
            self._boca.estado("musica")
            self._boca.nivel(nivel)

    def _silencio_musica_boca(self) -> None:
        if self._boca is not None and self._estado_voz == "idle":
            self._boca.estado("idle")

    def on_down(self) -> None:
        if not self._grabando:
            self._tiempo_down = time.monotonic()
            self._grabando = True
            self._modo_toggle = False
            _beep(880)
            self._estado_boca("escuchando")
            self._grabador.iniciar()

    def on_up(self) -> None:
        if not self._grabando:
            return
        if not self._modo_toggle:
            duracion = time.monotonic() - self._tiempo_down
            if duracion < self._umbral_toggle_s:
                # toque corto: pasa a modo dictado, sigue grabando
                self._modo_toggle = True
                return
        # push-to-talk soltado, o segundo toque que cierra el modo dictado
        self._grabando = False
        self._modo_toggle = False
        _beep(440)
        audio = self._grabador.detener()
        self._procesar(audio)

    def _procesar(self, audio: np.ndarray) -> None:
        if audio.size < self._config["audio"]["muestreo_hz"] * 0.2:
            print("(audio demasiado corto, se ignora)")
            self._estado_boca("idle")
            return
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < 0.001:
            # Silencio total (mic apagado/muteado): ni vale la pena mandarlo
            # a Whisper -- con silencio de verdad, alucina cosas como
            # "gracias" en vez de devolver texto vacío. Cortar acá evita
            # esa clase entera de falso positivo.
            print(f"(audio en silencio, rms={rms:.5f} -- ¿mic apagado/mute? no se transcribe)")
            self._plataforma.notificar("(silencio: ¿el micrófono está apagado?)")
            self._estado_boca("idle")
            return
        self._estado_boca("pensando")
        t0 = time.monotonic()
        texto = self._stt.transcribir(audio)
        t1 = time.monotonic()
        print(f"transcripción ({t1 - t0:.2f}s): {texto!r}")
        self._plataforma.notificar(texto or "(no se entendió nada)")
        if not texto:
            self._estado_boca("idle")
            return
        respuesta = self._cerebro.responder(texto)
        t2 = time.monotonic()
        print(f"cerebro ({t2 - t1:.2f}s): {respuesta!r}")
        if not respuesta:
            print("(silencio intencional, no hay nada que decir)")
            self._estado_boca("idle")
            return
        self._estado_boca("hablando")
        self._tts.hablar(respuesta, on_nivel=self._nivel_boca)
        self._estado_boca("idle")
        t3 = time.monotonic()
        print(f"tts ({t3 - t2:.2f}s), total ({t3 - t0:.2f}s)")

    def _nivel_boca(self, nivel: float) -> None:
        if self._boca is not None:
            self._boca.nivel(nivel)

    def correr(self) -> None:
        self._plataforma.escuchar_tecla(self.on_down, self.on_up)
        print(f"Tero escuchando. Mantené {self._config['tecla']['nombre']} para hablar.")
        try:
            while True:
                time.sleep(0.5)
                self._actualizar_cancion_boca()
        except KeyboardInterrupt:
            print("\nChau.")

    def _actualizar_cancion_boca(self) -> None:
        # Cada ~5s (no en cada tick de 0.5s) para no golpear la API de
        # Spotify de más -- se manda siempre (no solo cuando cambia el
        # tema) porque el progreso avanza en cada poll y la boca lo usa
        # para resincronizar la barra que interpola entre actualizaciones.
        if self._boca is None:
            return
        ahora = time.monotonic()
        if ahora - self._ultimo_poll_cancion < 5.0:
            return
        self._ultimo_poll_cancion = ahora
        info = musica.estado_reproduccion()
        if info is None or info["reproduciendo"]:
            self._pausado_desde = None
        else:
            if self._pausado_desde is None:
                self._pausado_desde = ahora
            elif ahora - self._pausado_desde >= 30.0:
                # Pausado hace rato: se oculta el reproductor entero (como si
                # no hubiera nada cargado), no solo se lo deja congelado en
                # pausa para siempre.
                info = None
        self._boca.cancion(info)


def main() -> None:
    with open(RUTA_CONFIG, "rb") as f:
        config = tomllib.load(f)
    Tero(config).correr()


if __name__ == "__main__":
    main()
