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

import threading
import time
import tomllib
from pathlib import Path

import numpy as np
import sounddevice as sd

import salud
from soul_connector.audio_sistema import MonitorAudioSistema
from soul_connector.server import ServidorSoulConnector
from cerebro.router import Cerebro
from herramientas import musica, youtube
from herramientas._ducking import Ducker
from plataforma import crear_plataforma
from voz.stt import LocalSTT, STTHibrido, groq_configurado
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
        # Opcional: nivel del mic en vivo mientras graba, para que el soul-connector
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
            config["audio"]["muestreo_hz"], config["audio"]["canales"], on_nivel=self._nivel_soul_connector
        )
        # El soul-connector primero, antes que los modelos: así puede mostrar en qué
        # etapa del arranque va, en vez de quedarse dibujando una onda que
        # parece lista y no responde.
        self._soul_connector = self._crear_soul_connector()
        # Antes de tocar self._stt/_tts: _decir() (el aviso hablado de
        # Groq <-> local) pasa por _estado_soul_connector(), que necesita las dos.
        self._estado_voz = "idle"
        self._ducker = Ducker()

        if groq_configurado():
            # Ni se carga Whisper acá: con Groq de por medio, el uso
            # normal de un push-to-talk personal ni se acerca al límite
            # del plan gratis (2000 pedidos/día), así que Whisper local
            # queda de respaldo para cuando falla Internet, cargado recién
            # la primera vez que hace falta -- ver voz/stt.py.
            self._carga_soul_connector("Transcripción por Groq (online)", 0.0)
            self._stt = STTHibrido(
                config["stt"], on_aviso=self._decir, on_carga=self._carga_soul_connector,
                muestreo_hz=config["audio"]["muestreo_hz"],
            )
            print("STT: Groq online, Whisper local de respaldo si hace falta.")
        else:
            self._carga_soul_connector("Cargando transcripción", 0.0)
            print("Cargando modelo de transcripción...")
            self._stt = LocalSTT(**config["stt"])
        self._carga_soul_connector("Cargando voz", 1 / 3)
        print("Cargando voz...")
        self._tts = TTS(**config["tts"])
        self._cerebro = Cerebro(**config["cerebro"])
        # Antes de decir "escuchando": recién reiniciada la máquina, cargar
        # el modelo lleva más que el timeout de una consulta (ver
        # cerebro/router.py), y el primer pedido fallaba con "se colgó el
        # modelo local".
        self._carga_soul_connector("Cargando modelo de lenguaje", 2 / 3)
        print("Cargando modelo de lenguaje...")
        self._cerebro.precargar()
        self._carga_soul_connector(None)
        self._monitor_audio = self._crear_monitor_audio()
        self._motivo_corte: str | None = None
        self._monitor_salud = salud.MonitorSalud(
            on_critico=self._salud_critica, on_aviso=self._salud_aviso
        )

        self._grabando = False
        self._modo_toggle = False
        self._tiempo_down = 0.0
        self._umbral_toggle_s = config["tecla"]["umbral_toggle_s"]
        # Barge-in: se activa en on_down si en ese momento _estado_voz es
        # "hablando" -- voz/tts.py lo revisa en cada bloque de audio y
        # corta ya mismo. Se limpia al arrancar cada _procesar() propio,
        # así cada turno arranca con la bandera en cero para su propio TTS.
        self._cancelar_tts = threading.Event()

        self._ultimo_poll_cancion = 0.0
        # Cuándo empezó la pausa actual (None si no está pausado o no hay
        # nada cargado). Sirve para ocultar el reproductor del soul-connector si
        # queda pausado mucho tiempo -- mostrar "pausado" para siempre
        # después de que el usuario se olvidó de la música es ruido visual
        # que no aporta nada.
        self._pausado_desde: float | None = None

    def _crear_soul_connector(self) -> ServidorSoulConnector | None:
        # El soul-connector es un cliente opcional: si esto falla por lo que sea, el
        # daemon tiene que seguir funcionando igual, sin ventana.
        try:
            return ServidorSoulConnector()
        except Exception as error:
            print(f"(soul-connector no disponible: {error})")
            return None

    def _carga_soul_connector(self, texto: str | None, progreso: float = 0.0) -> None:
        if self._soul_connector is not None:
            self._soul_connector.carga(texto, progreso)

    def _crear_monitor_audio(self) -> MonitorAudioSistema | None:
        # Igual que el soul-connector: opcional, el daemon tiene que andar sin esto.
        try:
            return MonitorAudioSistema(
                on_nivel=self._nivel_musica_soul_connector, on_silencio=self._silencio_musica_soul_connector
            )
        except Exception as error:
            print(f"(monitor de audio del sistema no disponible: {error})")
            return None

    def _estado_soul_connector(self, nombre: str) -> None:
        anterior = self._estado_voz
        self._estado_voz = nombre
        # Duckear música mientras se escucha/piensa/habla (no solo mientras
        # se graba): si se restaurara el volumen entre "pensando" y
        # "hablando" se oiría un salto para arriba y otro para abajo justo
        # antes de que Tero conteste. Solo se toca en las transiciones
        # hacia/desde idle -- entre estados no-idle no hay nada que hacer.
        if nombre != "idle" and anterior == "idle":
            self._ducker.activar()
        elif nombre == "idle" and anterior != "idle":
            self._ducker.desactivar()
        if self._soul_connector is not None:
            self._soul_connector.estado(nombre)

    def _decir(self, texto: str) -> None:
        """Habla un aviso fuera del flujo normal de turno (ej. Groq <-> Whisper
        local), sin pasar por el cerebro ni por una grabación del usuario."""
        anterior = self._estado_voz
        self._estado_soul_connector("hablando")
        self._tts.hablar(texto, on_nivel=self._nivel_soul_connector)
        self._estado_soul_connector(anterior)

    def _nivel_musica_soul_connector(self, nivel: float) -> None:
        # Solo si Tero no está en medio de escuchar/pensar/hablar -- eso
        # siempre tiene prioridad visual sobre "hay música sonando".
        if self._soul_connector is not None and self._estado_voz == "idle":
            self._soul_connector.estado("musica")
            self._soul_connector.nivel(nivel)

    def _silencio_musica_soul_connector(self) -> None:
        if self._soul_connector is not None and self._estado_voz == "idle":
            self._soul_connector.estado("idle")

    def on_down(self) -> None:
        if self._grabando:
            return
        if self._estado_voz == "pensando":
            # Transcribiendo o esperando al cerebro: todavía no hay nada
            # sonando que cortar, y arrancar a grabar en paralelo pisaría
            # el turno en curso (dos _procesar() a la vez, dos TTS
            # compitiendo). Se ignora el toque hasta que pase a "hablando"
            # o vuelva a "idle" -- ver barge-in más abajo para el caso que
            # sí se puede interrumpir.
            return
        if self._estado_voz == "hablando":
            # Barge-in: no esperar a que termine de hablar. voz/tts.py
            # revisa esta bandera en cada bloque de audio y corta el
            # sonido ya mismo (ver TTS.hablar). El propio _procesar() del
            # turno interrumpido nota que se canceló y no pisa el estado
            # "escuchando" que se pone dos líneas más abajo.
            self._cancelar_tts.set()
        self._tiempo_down = time.monotonic()
        self._grabando = True
        self._modo_toggle = False
        _beep(880)
        self._estado_soul_connector("escuchando")
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
        # En un hilo aparte: así el hilo que lee la tecla (plataforma/linux.py)
        # queda libre para notar un nuevo apretón mientras STT/cerebro/TTS
        # corren -- sin esto, el barge-in de on_down no podría dispararse
        # nunca porque el mismo hilo estaría ocupado adentro de _procesar().
        threading.Thread(target=self._procesar, args=(audio,), daemon=True).start()

    def _procesar(self, audio: np.ndarray) -> None:
        if audio.size < self._config["audio"]["muestreo_hz"] * 0.2:
            print("(audio demasiado corto, se ignora)")
            self._estado_soul_connector("idle")
            return
        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < 0.001:
            # Silencio total (mic apagado/muteado): ni vale la pena mandarlo
            # a Whisper -- con silencio de verdad, alucina cosas como
            # "gracias" en vez de devolver texto vacío. Cortar acá evita
            # esa clase entera de falso positivo.
            print(f"(audio en silencio, rms={rms:.5f} -- ¿mic apagado/mute? no se transcribe)")
            self._plataforma.notificar("(silencio: ¿el micrófono está apagado?)")
            self._estado_soul_connector("idle")
            return
        self._cancelar_tts.clear()
        self._estado_soul_connector("pensando")
        t0 = time.monotonic()
        texto = self._stt.transcribir(audio)
        t1 = time.monotonic()
        print(f"transcripción ({t1 - t0:.2f}s): {texto!r}")
        self._plataforma.notificar(texto or "(no se entendió nada)")
        if not texto:
            self._estado_soul_connector("idle")
            return
        respuesta = self._cerebro.responder(texto)
        t2 = time.monotonic()
        print(f"cerebro ({t2 - t1:.2f}s): {respuesta!r}")
        if not respuesta:
            print("(silencio intencional, no hay nada que decir)")
            self._estado_soul_connector("idle")
            return
        self._estado_soul_connector("hablando")
        self._tts.hablar(respuesta, on_nivel=self._nivel_soul_connector, cancelar=self._cancelar_tts)
        if not self._cancelar_tts.is_set():
            self._estado_soul_connector("idle")
        # Si se canceló, on_down ya puso el estado en "escuchando" para el
        # turno que interrumpió a este -- no pisarlo con "idle" acá.
        t3 = time.monotonic()
        print(f"tts ({t3 - t2:.2f}s), total ({t3 - t0:.2f}s)")

    def _nivel_soul_connector(self, nivel: float) -> None:
        if self._soul_connector is not None:
            self._soul_connector.nivel(nivel)

    def _salud_critica(self, motivo: str) -> None:
        # Solo deja el pedido anotado: cerrar de verdad lo hace el bucle
        # principal, que es el dueño del proceso. Cerrar desde el hilo del
        # monitor dejaría a medias lo que esté pasando (una grabación
        # abierta, el TTS hablando).
        print(f"\n*** Cerrando Tero para cuidar el equipo: {motivo}")
        self._plataforma.notificar(f"Cierro Tero: {motivo}")
        self._motivo_corte = motivo

    def _salud_aviso(self, texto: str) -> None:
        print(f"(salud: {texto})")
        self._plataforma.notificar(texto)

    def correr(self) -> None:
        self._plataforma.escuchar_tecla(self.on_down, self.on_up)
        self._monitor_salud.arrancar()
        print(f"Tero escuchando. Mantené {self._config['tecla']['nombre']} para hablar.")
        try:
            while self._motivo_corte is None:
                time.sleep(0.5)
                self._actualizar_cancion_soul_connector()
        except KeyboardInterrupt:
            print("\nChau.")
            return
        # Se sale por el monitor de salud: código propio para que el
        # lanzador lo distinga de una caída de verdad.
        sys.exit(salud.CODIGO_SALIDA_SALUD)

    def _actualizar_cancion_soul_connector(self) -> None:
        # Cada ~5s (no en cada tick de 0.5s) para no golpear la API de
        # Spotify de más -- se manda siempre (no solo cuando cambia el
        # tema) porque el progreso avanza en cada poll y el soul-connector lo usa
        # para resincronizar la barra que interpola entre actualizaciones.
        if self._soul_connector is None:
            return
        ahora = time.monotonic()
        if ahora - self._ultimo_poll_cancion < 5.0:
            return
        self._ultimo_poll_cancion = ahora
        info = musica.estado_reproduccion()
        if info is None:
            # Nada sonando en Spotify: si hay un canal de YouTube abierto,
            # mostrar eso en su lugar -- mismo casillero de la interfaz,
            # una sola cosa a la vez (Spotify tiene prioridad si las dos
            # cosas están activas, caso raro).
            info = youtube.estado_actual()
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
        self._soul_connector.cancion(info)


def main() -> None:
    with open(RUTA_CONFIG, "rb") as f:
        config = tomllib.load(f)
    Tero(config).correr()


if __name__ == "__main__":
    main()
