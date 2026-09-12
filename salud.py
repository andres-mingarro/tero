"""Vigilancia de salud del sistema: avisa y corta Tero si el equipo entra
en un estado que pone en riesgo la estabilidad de la sesión.

Lo primero, porque es la pregunta que originó este archivo: **Tero no
puede quemar la placa de video.** El control térmico de la GPU vive en el
firmware, por debajo del sistema operativo: la placa se frena sola
(slowdown) al llegar a su límite y se apaga sola si lo pasa, y ningún
proceso puede desactivar eso. En esta máquina (RTX 5060 Laptop) el límite
de fábrica es 87 C, con slowdown por hardware 2 C más arriba y apagado 5 C
más arriba. Un proceso de Python no tiene forma de saltearse eso.

Los riesgos reales son otros, y son de estabilidad, no de hardware:

1. **Quedarse sin RAM.** Es el único que puede colgar el sistema entero.
   Whisper large-v3 y Ollama no son livianos, y esta máquina tiene 14 GB.
   Cuando `MemAvailable` se acerca a cero, Linux empieza a swapear sin
   parar y el escritorio queda inusable varios minutos antes de que el
   OOM killer decida a quién matar (y puede no elegir a Tero).
2. **Quedarse sin VRAM.** Ya pasó: arrancar un segundo daemon mató al
   segundo con `CUDA out of memory`. No rompe nada, pero como esta GPU
   además maneja el escritorio, la presión de VRAM puede hacer que las
   aplicaciones del entorno se pongan lentas o se caigan.

De ahí el criterio de este módulo: **la RAM corta, lo térmico solo avisa.**
Cortar por temperatura sería redundante con lo que la placa ya hace sola,
así que se avisa (por pedido explícito del usuario, que quiere enterarse)
pero solo se corta si el slowdown por hardware se sostiene mucho rato, que
significa que la placa viene frenada y el equipo probablemente esté con un
problema de ventilación.

Nada de esto se dice por voz: si el problema es justamente que falta
memoria, levantar el TTS para anunciarlo la empeora. Va por notificación
de escritorio y por el log.
"""

import subprocess
import threading
import time
from typing import Callable

CODIGO_SALIDA_SALUD = 3  # lo reconoce el lanzador ./tero

_INTERVALO_S = 5.0

# MemAvailable (no "free"): es la estimación del kernel de cuánto se puede
# pedir sin entrar a swapear, que es la cuenta que importa acá.
_RAM_CRITICA_MB = 400
_RAM_AVISO_MB = 1200

_VRAM_AVISO_MB = 200

# Cuántas muestras seguidas hacen falta para actuar. Un pico puntual
# (Chrome abriendo algo pesado) no tiene que cortar una conversación a la
# mitad; un problema real se sostiene.
_MUESTRAS_CORTE_RAM = 3  # ~15s
_MUESTRAS_CORTE_TERMICO = 12  # ~60s de slowdown por hardware sostenido
_MUESTRAS_AVISO = 2

# Para no repetir la misma notificación cada 5 segundos.
_SILENCIO_ENTRE_AVISOS_S = 300.0


def _ram_disponible_mb() -> float | None:
    try:
        with open("/proc/meminfo") as f:
            for linea in f:
                if linea.startswith("MemAvailable:"):
                    return int(linea.split()[1]) / 1024
    except Exception:
        return None
    return None


def _gpu() -> dict | None:
    """Temperatura, VRAM libre y si la placa se está frenando sola.

    `clocks_throttle_reasons.hw_thermal_slowdown` es mejor señal que
    comparar la temperatura contra un número fijo: es la placa diciendo
    "estoy en mi límite", con el límite real de esta placa, sin que haya
    que adivinar un umbral por modelo.
    """
    try:
        resultado = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,memory.total,memory.used,"
                "clocks_throttle_reasons.hw_thermal_slowdown",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if resultado.returncode != 0:
            return None
        temp, total, usada, slowdown = (p.strip() for p in resultado.stdout.split(","))
        return {
            "temperatura_c": float(temp),
            "vram_libre_mb": float(total) - float(usada),
            "slowdown_termico": slowdown.lower() == "active",
        }
    except Exception:
        # Sin nvidia-smi (equipo sin GPU NVIDIA) no hay nada que vigilar
        # acá; la vigilancia de RAM sigue andando igual.
        return None


class MonitorSalud:
    """Muestrea el estado del equipo en segundo plano y avisa o corta.

    `on_critico(motivo)` se llama una sola vez, y es quien decide cómo
    cerrar -- este módulo no llama a sys.exit por su cuenta para no dejar
    el daemon a medio cerrar desde un hilo cualquiera.
    """

    def __init__(
        self,
        on_critico: Callable[[str], None],
        on_aviso: Callable[[str], None],
        intervalo_s: float = _INTERVALO_S,
    ):
        self._on_critico = on_critico
        self._on_aviso = on_aviso
        self._intervalo_s = intervalo_s
        self._detener = threading.Event()
        self._ya_corte = False
        self._seguidas: dict[str, int] = {}
        self._ultimo_aviso: dict[str, float] = {}

    def arrancar(self) -> None:
        threading.Thread(target=self._bucle, daemon=True).start()

    def detener(self) -> None:
        self._detener.set()

    def _contar(self, clave: str, activo: bool) -> int:
        self._seguidas[clave] = self._seguidas.get(clave, 0) + 1 if activo else 0
        return self._seguidas[clave]

    def _avisar(self, clave: str, texto: str) -> None:
        ahora = time.monotonic()
        if ahora - self._ultimo_aviso.get(clave, -_SILENCIO_ENTRE_AVISOS_S) < _SILENCIO_ENTRE_AVISOS_S:
            return
        self._ultimo_aviso[clave] = ahora
        self._on_aviso(texto)

    def _cortar(self, motivo: str) -> None:
        if self._ya_corte:
            return
        self._ya_corte = True
        self._on_critico(motivo)

    def _bucle(self) -> None:
        while not self._detener.wait(self._intervalo_s):
            try:
                self._revisar()
            except Exception as error:
                # El vigilante nunca puede ser el que rompa el daemon.
                print(f"(monitor de salud falló, se sigue sin él: {error})")
                return

    def _revisar(self) -> None:
        ram_mb = _ram_disponible_mb()
        if ram_mb is not None:
            if self._contar("ram_critica", ram_mb < _RAM_CRITICA_MB) >= _MUESTRAS_CORTE_RAM:
                self._cortar(
                    f"queda muy poca memoria libre ({ram_mb:.0f} MB). "
                    "Si sigo, el equipo entero se puede colgar swapeando."
                )
                return
            if self._contar("ram_baja", ram_mb < _RAM_AVISO_MB) >= _MUESTRAS_AVISO:
                self._avisar("ram_baja", f"Queda poca memoria libre ({ram_mb:.0f} MB).")

        gpu = _gpu()
        if gpu is None:
            return

        if self._contar("termico", gpu["slowdown_termico"]) >= _MUESTRAS_CORTE_TERMICO:
            self._cortar(
                f"la placa de video viene frenándose sola por temperatura "
                f"({gpu['temperatura_c']:.0f} C) hace un rato largo. No hay riesgo de "
                "rotura (eso lo maneja el firmware), pero conviene dejarla enfriar."
            )
            return
        if self._seguidas.get("termico", 0) >= _MUESTRAS_AVISO:
            self._avisar(
                "termico",
                f"La placa se está frenando sola por temperatura ({gpu['temperatura_c']:.0f} C).",
            )

        if self._contar("vram", gpu["vram_libre_mb"] < _VRAM_AVISO_MB) >= _MUESTRAS_AVISO:
            self._avisar(
                "vram",
                f"Queda poca memoria de video ({gpu['vram_libre_mb']:.0f} MB); "
                "el escritorio puede ponerse lento.",
            )
