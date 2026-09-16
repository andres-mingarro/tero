"""Interfaz de plataforma: todo lo que depende del sistema operativo pasa por acá.

El resto del programa nunca importa nada de Windows/Linux directamente,
solo instancia la implementación correcta y usa esta interfaz.
"""

from abc import ABC, abstractmethod
from typing import Callable


class Plataforma(ABC):
    @abstractmethod
    def escuchar_tecla(
        self,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_atajo_chatgpt: Callable[[], None] | None = None,
    ) -> None:
        """Arranca un listener en segundo plano de la tecla de activación.

        Llama a on_down cuando se aprieta y on_up cuando se suelta.
        Si se pasa on_atajo_chatgpt, además dispara eso una sola vez por
        combinación cuando se detecta Ctrl+Shift juntas (hand-off directo
        a ChatGPT, sin pasar por el cerebro -- ver herramientas/codex.py
        para el hand-off equivalente con Codex).
        No bloquea: corre en su propio hilo.
        """

    @abstractmethod
    def notificar(self, texto: str) -> None:
        """Muestra una notificación de escritorio."""
