"""Servidor WebSocket de la boca: difunde estado (idle/escuchando/pensando/
hablando) y nivel de audio (RMS) a quien esté conectado.

El daemon tiene que funcionar sin la boca (ver README de la fase): si no
hay ningún cliente conectado, o si el servidor ni siquiera pudo arrancar
(puerto ocupado, etc.), emitir un estado/nivel no hace nada — nunca
lanza, nunca bloquea el resto de Tero.
"""

import asyncio
import json
import threading

import websockets

PUERTO = 8765


class ServidorBoca:
    def __init__(self, puerto: int = PUERTO):
        self._puerto = puerto
        self._clientes: set = set()
        self._loop = asyncio.new_event_loop()
        self._listo = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()
        self._listo.wait(timeout=3)

    def _correr(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._servir())
        self._listo.set()
        self._loop.run_forever()

    async def _servir(self) -> None:
        async def manejar(ws) -> None:
            self._clientes.add(ws)
            print("(boca: cliente conectado)")
            try:
                await ws.wait_closed()
            finally:
                self._clientes.discard(ws)
                print("(boca: cliente desconectado)")

        try:
            await websockets.serve(manejar, "127.0.0.1", self._puerto)
        except OSError:
            # Puerto ocupado (ej. otra instancia del daemon corriendo):
            # la boca simplemente no tiene con quién hablar, no es fatal.
            pass

    def _difundir(self, mensaje: dict) -> None:
        if not self._clientes:
            return
        datos = json.dumps(mensaje)

        async def _enviar_a_todos() -> None:
            muertos = set()
            for cliente in list(self._clientes):
                try:
                    await cliente.send(datos)
                except Exception:
                    muertos.add(cliente)
            self._clientes.difference_update(muertos)

        asyncio.run_coroutine_threadsafe(_enviar_a_todos(), self._loop)

    def estado(self, nombre: str) -> None:
        """nombre: 'idle' | 'escuchando' | 'pensando' | 'hablando'."""
        self._difundir({"estado": nombre})

    def nivel(self, valor: float) -> None:
        """valor: RMS del audio en reproducción, 0.0-1.0 aproximado."""
        self._difundir({"nivel": valor})

    def cancion(self, info: dict | None) -> None:
        """info: {"texto", "progreso_ms", "duracion_ms"} o None si no suena
        nada. La boca interpola el progreso entre actualizaciones."""
        self._difundir({"cancion": info})
