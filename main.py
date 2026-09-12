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
    os.execv(sys.executable, [sys.executable] + sys.argv)


_asegurar_libs_cuda()

import time
import tomllib
from pathlib import Path

import numpy as np
import sounddevice as sd

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

    def __init__(self, muestreo_hz: int, canales: int):
        self._muestreo_hz = muestreo_hz
        self._canales = canales
        self._trozos: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, tiempo, status):
        self._trozos.append(indata.copy())

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
        self._grabador = Grabador(config["audio"]["muestreo_hz"], config["audio"]["canales"])
        print("Cargando modelo de transcripción...")
        self._stt = STT(**config["stt"])
        print("Cargando voz...")
        self._tts = TTS(**config["tts"])

        self._grabando = False
        self._modo_toggle = False
        self._tiempo_down = 0.0
        self._umbral_toggle_s = config["tecla"]["umbral_toggle_s"]

    def on_down(self) -> None:
        if not self._grabando:
            self._tiempo_down = time.monotonic()
            self._grabando = True
            self._modo_toggle = False
            _beep(880)
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
            return
        t0 = time.monotonic()
        texto = self._stt.transcribir(audio)
        t1 = time.monotonic()
        print(f"transcripción ({t1 - t0:.2f}s): {texto!r}")
        self._plataforma.notificar(texto or "(no se entendió nada)")
        if not texto:
            return
        self._tts.hablar(f"Dijiste: {texto}")
        t2 = time.monotonic()
        print(f"tts ({t2 - t1:.2f}s), total ({t2 - t0:.2f}s)")

    def correr(self) -> None:
        self._plataforma.escuchar_tecla(self.on_down, self.on_up)
        print(f"Tero escuchando. Mantené {self._config['tecla']['nombre']} para hablar.")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nChau.")


def main() -> None:
    with open(RUTA_CONFIG, "rb") as f:
        config = tomllib.load(f)
    Tero(config).correr()


if __name__ == "__main__":
    main()
