"""Implementación de Plataforma para Linux."""

import selectors
import subprocess
import threading
from typing import Callable

from evdev import InputDevice, ecodes, list_devices

from plataforma.base import Plataforma


_EJES_PUNTERO = {ecodes.REL_X, ecodes.REL_Y}


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

    def escuchar_tecla(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        self._detener.clear()
        self._hilo = threading.Thread(
            target=self._bucle_tecla, args=(on_down, on_up), daemon=True
        )
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()

    def _bucle_tecla(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
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
        try:
            while not self._detener.is_set():
                for clave, _ in selector.select(timeout=0.2):
                    dispositivo = clave.fileobj
                    for evento in dispositivo.read():
                        if evento.type != ecodes.EV_KEY or evento.code != self._codigo_tecla:
                            continue
                        if evento.value == 1:  # tecla apretada
                            on_down()
                        elif evento.value == 0:  # tecla soltada
                            on_up()
        finally:
            for dispositivo in dispositivos:
                selector.unregister(dispositivo)
                dispositivo.close()

    def notificar(self, texto: str) -> None:
        subprocess.run(["notify-send", "Tero", texto], check=False)
