"""Cerebro de Tero: un modelo local elige y ejecuta herramientas (tool calling).

Se usa qwen3:4b-instruct y no qwen3:4b a secas: la variante base fuerza un
bloque <think> en su plantilla de chat pase lo que pase (aunque se pida
think=False), lo que agregaba ~15-25s de razonamiento visible a cada
respuesta. La variante instruct no razona y responde en <1s en caliente.

Dos salvaguardas duras, no opcionales, sobre cada llamada al modelo:
- `num_predict`: sin tope, se vio a este modelo entrar en bucle y generar
  30000+ tokens sin parar (nunca emitió el token de fin), saturando la GPU
  varios minutos con una sola consulta.
- `timeout` de cliente: red de más para el caso en que, aun con el tope de
  tokens, algo se cuelgue del lado del servidor.
"""

from ollama import Client

import herramientas.clima  # noqa: F401  (registra la herramienta)
import herramientas.volumen  # noqa: F401
import herramientas.web  # noqa: F401
from cerebro.prompt import PROMPT_SISTEMA
from herramientas import catalogo, ejecutar

_MAX_TOKENS_RESPUESTA = 200
_TIMEOUT_S = 10.0


class Cerebro:
    def __init__(self, modelo: str = "qwen3:4b-instruct"):
        self._modelo = modelo
        self._cliente = Client(timeout=_TIMEOUT_S)

    def _chat(self, mensajes: list, con_herramientas: bool):
        return self._cliente.chat(
            model=self._modelo,
            messages=mensajes,
            tools=catalogo() if con_herramientas else None,
            options={"num_predict": _MAX_TOKENS_RESPUESTA},
        )

    def responder(self, texto_usuario: str) -> str:
        mensajes = [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": texto_usuario},
        ]
        try:
            respuesta = self._chat(mensajes, con_herramientas=True)
        except Exception as error:
            return f"Se colgó el modelo local: {error}"
        mensaje = respuesta.message
        if not mensaje.tool_calls:
            return (mensaje.content or "").strip()

        mensajes.append(mensaje)
        vistas = set()
        for llamada in mensaje.tool_calls:
            clave = (llamada.function.name, tuple(sorted(llamada.function.arguments.items())))
            if clave in vistas:
                # Qwen3 4B a veces repite la misma llamada varias veces
                # (visto con "bajame el volumen 20%" -> 3 llamadas idénticas).
                continue
            vistas.add(clave)
            resultado = ejecutar(llamada.function.name, llamada.function.arguments)
            mensajes.append(
                {"role": "tool", "content": resultado, "name": llamada.function.name}
            )

        try:
            respuesta_final = self._chat(mensajes, con_herramientas=False)
        except Exception as error:
            return f"Se colgó el modelo local: {error}"
        return (respuesta_final.message.content or "").strip()
