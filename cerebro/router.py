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

import re

from ollama import Client

import herramientas.celular  # noqa: F401
import herramientas.clima  # noqa: F401  (registra la herramienta)
import herramientas.mapas  # noqa: F401
import herramientas.musica  # noqa: F401
import herramientas.terminal  # noqa: F401
import herramientas.tiempo  # noqa: F401
import herramientas.volumen  # noqa: F401
import herramientas.web  # noqa: F401
from cerebro.prompt import PROMPT_SISTEMA
from herramientas import catalogo, ejecutar, es_error, sin_argumentos_obligatorios

_MAX_TOKENS_RESPUESTA = 200
_TIMEOUT_S = 10.0

# Cuántas rondas de tool calling se permiten en un mismo turno. Sin esto,
# un pedido compuesto ("buscá X y mandámelo al celular") quedaba a medias:
# el modelo llamaba una sola herramienta y después inventaba en el resumen
# que había hecho la segunda acción también. Con varias rondas puede pedir
# una herramienta, ver el resultado, y pedir otra si todavía hace falta.
# Tope bajo a propósito: cada ronda es una llamada al modelo entera.
_MAX_RONDAS_HERRAMIENTAS = 4

# Para estas herramientas la confirmación hablada sobra: la música que
# empieza a sonar (o el corte de audio) ya es la respuesta. Si alguna
# falla, se rompe el silencio igual (ver más abajo) para avisar que algo
# pasó.
_HERRAMIENTAS_SILENCIOSAS = {"reproducir_musica", "reproducir_musica_aleatoria", "control_media"}

# Cuántos intercambios (usuario+asistente) previos se le pasan al modelo.
# Sin esto cada frase arranca de cero: "poné metallica" -> "pausalo" no
# tiene forma de saber a qué se refiere "lo". No hace falta mucho margen
# (esto no reemplaza preguntas de seguimiento, que siguen prohibidas: el
# usuario tiene que decir algo con sentido propio, no un "sí"/"no" que
# dependa de que el modelo recuerde qué preguntó — acá al menos si recuerda).
_TURNOS_HISTORIAL = 2


_ACCIONES_MEDIA = {"reproducir", "pausar", "siguiente", "anterior"}


def _herramienta_dicha_en_vez_de_llamada(texto: str) -> tuple[str, dict] | None:
    """Detecta cuando el modelo dijo en voz el nombre de una acción o de
    una herramienta en vez de invocarla -- visto en vivo tres veces, cada
    vez con una firma de texto distinta ("cambiá de canción" -> dijo
    'reproducir siguiente'; "poné play" -> dijo 'reproducir'; "ponme
    play" -> dijo literalmente 'reproducir_musica_aleatoria'), pese a que
    la temperatura ya está baja (0.2) y el prompt ya dice que hay que usar
    la herramienta. No es una falla que se arregle bajando más la
    temperatura (ya casi no hay margen) ni con otra frase de prompt (esa
    regla ya existe y falla igual) -- es la misma clase de error cada vez
    y tiene una firma exacta y detectable en código: la respuesta entera
    es, literal, el nombre de una acción o de una herramienta real. Se
    ejecuta la herramienta correspondiente en vez de tratarlo como charla.

    Devuelve (nombre_herramienta, argumentos) o None si no aplica. Solo
    contempla herramientas sin argumentos obligatorios (más el caso
    puntual de control_media, cuyo único argumento es la propia acción
    que el modelo dijo).
    """
    limpio = texto.strip().lower().rstrip(".!?")
    if limpio in _ACCIONES_MEDIA:
        return "control_media", {"accion": limpio}
    palabras = limpio.split()
    if len(palabras) == 2 and palabras[0] == "reproducir" and palabras[1] in _ACCIONES_MEDIA:
        return "control_media", {"accion": palabras[1]}
    if limpio in sin_argumentos_obligatorios():
        return limpio, {}
    return None


def _sin_pregunta_de_seguimiento(texto: str) -> str:
    """Corta cualquier pregunta colgada al final de una respuesta hablada.

    El prompt prohíbe preguntar, pero el modelo a veces lo hace igual
    (visto en vivo: 'de nada, ¿qué tal si ahora ponemos música?'). El
    problema no es solo que suene raro: esa pregunta queda en el
    historial, y el turno siguiente -- aunque no tenga nada que ver -- se
    interpreta como si fuera la respuesta a ella (visto en vivo: después
    de esa pregunta, "dos más dos" disparó reproducir_musica_aleatoria en
    vez de contestar la cuenta). Es un filtro determinístico, no otra
    regla más para que el modelo decida cumplir o no.
    """
    oraciones = re.split(r"(?<=[.!?])\s+", texto.strip())
    while oraciones and oraciones[-1].rstrip().endswith("?"):
        oraciones.pop()
    return " ".join(oraciones).strip() or texto.strip()


class Cerebro:
    def __init__(self, modelo: str = "qwen3:4b-instruct"):
        self._modelo = modelo
        self._cliente = Client(timeout=_TIMEOUT_S)
        self._historial: list[dict] = []

    def _chat(self, mensajes: list, con_herramientas: bool):
        return self._cliente.chat(
            model=self._modelo,
            messages=mensajes,
            tools=catalogo() if con_herramientas else None,
            # temperature baja: el muestreo por defecto a veces "describe"
            # la tool call en palabras en vez de emitirla de verdad (visto
            # en vivo con "siguiente tema" -> dijo "reproducir siguiente"),
            # sin ningún patrón fijo -- es aleatoriedad del muestreo, no un
            # bug de código. Menos temperatura = tool calling más confiable,
            # a costa de un poco menos de variedad en cómo habla.
            options={"num_predict": _MAX_TOKENS_RESPUESTA, "temperature": 0.2},
        )

    def responder(self, texto_usuario: str) -> str:
        respuesta_texto, entrada_historial = self._responder(texto_usuario)
        self._historial.append({"role": "user", "content": texto_usuario})
        self._historial.append(entrada_historial)
        self._historial = self._historial[-_TURNOS_HISTORIAL * 2 :]
        return respuesta_texto

    def _responder(self, texto_usuario: str) -> tuple[str, dict]:
        """Devuelve (lo que se dice en voz, el mensaje que queda en el historial).

        Cuando hubo una herramienta de por medio, el mensaje de historial
        va con role "system" y no "assistant": si quedara marcado como un
        mensaje del asistente, el modelo lo lee como un ejemplo de "cómo
        hablo yo" y termina copiando el formato tal cual en la respuesta
        hablada real (pasó de verdad, con dos búsquedas seguidas en Maps).
        Para una respuesta charlada sin herramienta, sí va como
        "assistant" — ahí no hay riesgo, es lo que genuinamente dijiste.
        """
        mensajes = [
            {"role": "system", "content": PROMPT_SISTEMA},
            *self._historial,
            {"role": "user", "content": texto_usuario},
        ]
        vistas: set = set()
        resultados: list = []

        for ronda in range(_MAX_RONDAS_HERRAMIENTAS):
            try:
                respuesta = self._chat(mensajes, con_herramientas=True)
            except Exception as error:
                texto = f"Se colgó el modelo local: {error}"
                return texto, {"role": "assistant", "content": texto}
            mensaje = respuesta.message
            if not mensaje.tool_calls:
                if ronda == 0:
                    # Nunca pidió ninguna herramienta: charla directa.
                    texto = _sin_pregunta_de_seguimiento((mensaje.content or "").strip())
                    llamada = _herramienta_dicha_en_vez_de_llamada(texto)
                    if llamada is not None:
                        nombre, argumentos = llamada
                        resultado = ejecutar(nombre, argumentos)
                        print(
                            f"  tool_call (fallback -- el modelo dijo la herramienta/acción "
                            f"en vez de llamarla): {nombre}({argumentos}) -> {resultado!r}"
                        )
                        if es_error(resultado):
                            return resultado, {"role": "assistant", "content": resultado}
                        if nombre in _HERRAMIENTAS_SILENCIOSAS:
                            entrada_historial = {
                                "role": "system",
                                "content": (
                                    f"Nota interna, no es una respuesta hablada: en el turno "
                                    f"anterior usaste la herramienta {nombre} y el resultado "
                                    f"fue: {resultado}."
                                ),
                            }
                            return "", entrada_historial
                        return resultado, {"role": "assistant", "content": resultado}
                    return texto, {"role": "assistant", "content": texto}
                break  # ya no pide más herramientas, pasa a resumir

            mensajes.append(mensaje)
            for llamada in mensaje.tool_calls:
                clave = (llamada.function.name, tuple(sorted(llamada.function.arguments.items())))
                if clave in vistas:
                    # Qwen3 4B a veces repite la misma llamada varias veces
                    # (visto con "bajame el volumen 20%" -> 3 llamadas idénticas).
                    continue
                vistas.add(clave)
                resultado = ejecutar(llamada.function.name, llamada.function.arguments)
                print(f"  tool_call: {llamada.function.name}({llamada.function.arguments}) -> {resultado!r}")
                resultados.append((llamada.function.name, llamada.function.arguments, resultado))
                mensajes.append(
                    {"role": "tool", "content": resultado, "name": llamada.function.name}
                )

        # Prosa simple, sin paréntesis ni llaves: un formato tipo código
        # (aunque sea rol "system") se le pegó al modelo como estilo a
        # imitar y terminó literalmente diciendo en voz alta algo como
        # 'control_media({"accion": "siguiente"})' en vez de ejecutar la
        # herramienta. Nada acá debe parecer sintaxis para copiar.
        descripcion_acciones = ". ".join(
            f"usaste la herramienta {nombre} y el resultado fue: {resultado}"
            for nombre, _args, resultado in resultados
        )
        entrada_historial = {
            "role": "system",
            "content": (
                f"Nota interna, no es una respuesta hablada: en el turno "
                f"anterior {descripcion_acciones}."
            ),
        }

        if resultados and all(
            nombre in _HERRAMIENTAS_SILENCIOSAS and not es_error(resultado)
            for nombre, _args, resultado in resultados
        ):
            return "", entrada_historial

        try:
            respuesta_final = self._chat(mensajes, con_herramientas=False)
        except Exception as error:
            return f"Se colgó el modelo local: {error}", entrada_historial
        texto = _sin_pregunta_de_seguimiento((respuesta_final.message.content or "").strip())
        return texto, entrada_historial
