"""Implementación de Plataforma para Linux."""

import selectors
import subprocess
import threading
from typing import Callable

from evdev import InputDevice, ecodes, list_devices

from plataforma.base import Plataforma


_EJES_PUNTERO = {ecodes.REL_X, ecodes.REL_Y}

# Solo el Ctrl DERECHO -- decisión explícita del usuario (2026-09-16): el
# izquierdo se usa todo el tiempo en shortcuts comunes de otras apps
# (Ctrl+Shift+T, Ctrl+Shift+N, Ctrl+Shift+Esc...), y sumarlo dispararía
# el atajo por accidente en medio del uso normal de la compu. El derecho
# no lo usa nada más -- mismo motivo por el que ya es la tecla de
# push-to-talk (config.toml, [tecla]). Efecto secundario aceptado: un
# toque de Ctrl+Shift con la mano derecha también hace sonar los dos
# beeps de push-to-talk (mismo evdev, no exclusivo) -- inofensivo, la
# grabación de menos de 0.2s que resulta se descarta sola (main.py).
_TECLAS_CTRL = {ecodes.KEY_RIGHTCTRL}
_TECLAS_SHIFT = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}


def _es_teclado(dispositivo: InputDevice) -> bool:
    """Filtra ratones/mandos: un teclado real tiene teclas alfabéticas y
    nunca reporta movimiento de puntero (REL_X/REL_Y). Excluir por
    cualquier EV_REL no alcanza: varios receptores combo (Logitech, Corsair)
    exponen la rueda de scroll (REL_HWHEEL) en la misma interfaz del
    teclado sin ser un mouse. Un mouse con botones programables remapeados
    a teclas (ej. G903) sigue teniendo REL_X/REL_Y porque es, de hecho,
    un mouse."""
    capacidades = dispositivo.capabilities()
    teclas = capacidades.get(ecodes.EV_KEY, [])
    tiene_alfabeto = ecodes.KEY_A in teclas and ecodes.KEY_Z in teclas
    ejes_rel = set(capacidades.get(ecodes.EV_REL, []))
    es_puntero = bool(ejes_rel & _EJES_PUNTERO)
    return tiene_alfabeto and not es_puntero


def _teclados() -> list[InputDevice]:
    dispositivos = []
    for ruta in list_devices():
        try:
            dispositivo = InputDevice(ruta)
        except OSError:
            continue
        if _es_teclado(dispositivo):
            dispositivos.append(dispositivo)
    return dispositivos


class PlataformaLinux(Plataforma):
    def __init__(self, tecla: str = "KEY_PAUSE"):
        self._codigo_tecla = getattr(ecodes, tecla)
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None

    def escuchar_tecla(
        self,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_atajo_chatgpt: Callable[[], None] | None = None,
    ) -> None:
        self._detener.clear()
        self._hilo = threading.Thread(
            target=self._bucle_tecla, args=(on_down, on_up, on_atajo_chatgpt), daemon=True
        )
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()

    def _bucle_tecla(
        self,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_atajo_chatgpt: Callable[[], None] | None,
    ) -> None:
        dispositivos = _teclados()
        if not dispositivos:
            raise RuntimeError(
                "No se encontró ningún teclado en /dev/input. "
                "¿El usuario está en el grupo 'input'? "
                "(sudo usermod -aG input $USER, después reloguear)"
            )
        selector = selectors.DefaultSelector()
        for dispositivo in dispositivos:
            selector.register(dispositivo, selectors.EVENT_READ)
        # Estado de teclas mantenidas, para detectar el atajo Ctrl+Shift
        # como acorde (las dos juntas, no una tecla física puntual --
        # ni Ctrl ni Shift solas alcanzan). "disparado" evita repetir el
        # callback mientras se sigan manteniendo apretadas; se limpia en
        # cuanto se suelta cualquiera de las dos.
        presionadas: set[int] = set()
        disparado = False
        try:
            while not self._detener.is_set():
                for clave, _ in selector.select(timeout=0.2):
                    dispositivo = clave.fileobj
                    for evento in dispositivo.read():
                        if evento.type != ecodes.EV_KEY:
                            continue
                        if evento.value == 1:  # tecla apretada
                            presionadas.add(evento.code)
                        elif evento.value == 0:  # tecla soltada
                            presionadas.discard(evento.code)

                        if evento.code == self._codigo_tecla:
                            if evento.value == 1:
                                on_down()
                            elif evento.value == 0:
                                on_up()

                        if on_atajo_chatgpt is not None:
                            acorde = bool(presionadas & _TECLAS_CTRL) and bool(
                                presionadas & _TECLAS_SHIFT
                            )
                            if acorde and not disparado:
                                disparado = True
                                on_atajo_chatgpt()
                            elif not acorde:
                                disparado = False
        finally:
            for dispositivo in dispositivos:
                selector.unregister(dispositivo)
                dispositivo.close()

    def notificar(self, texto: str) -> None:
        subprocess.run(["notify-send", "Tero", texto], check=False)
