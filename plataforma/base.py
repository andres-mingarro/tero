"""Interfaz de plataforma: todo lo que depende del sistema operativo pasa por acá.

El resto del programa nunca importa nada de Windows/Linux directamente,
solo instancia la implementación correcta y usa esta interfaz.
"""

from abc import ABC, abstractmethod
from typing import Callable


class Plataforma(ABC):
    @abstractmethod
    def escuchar_tecla(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        """Arranca un listener en segundo plano de la tecla de activación.

        Llama a on_down cuando se aprieta y on_up cuando se suelta.
        No bloquea: corre en su propio hilo.
        """

    @abstractmethod
    def ventana_activa(self) -> dict:
        """Devuelve info de la ventana con foco: {"titulo": str, "app": str}."""

    @abstractmethod
    def capturar_pantalla(self) -> bytes:
        """Captura la pantalla actual y devuelve PNG en bytes."""

    @abstractmethod
    def media(self, accion: str) -> None:
        """Envía una acción de control multimedia: play_pause, siguiente, anterior."""

    @abstractmethod
    def notificar(self, texto: str) -> None:
        """Muestra una notificación de escritorio."""
