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
import herramientas.youtube  # noqa: F401
from cerebro.prompt import PROMPT_SISTEMA
from herramientas import catalogo, ejecutar, es_error

_MAX_TOKENS_RESPUESTA = 200
_TIMEOUT_S = 10.0

# El calentamiento del modelo (ver `precargar`) va con un timeout aparte y
# mucho más largo. Con los 10s de arriba, recién reiniciada la máquina, el
# cliente cortaba antes de que terminara, y Ollama *aborta* lo que estaba
# haciendo cuando el cliente se desconecta ("client connection closed
# before llama-server finished loading, aborting load"). El pedido
# siguiente empezaba de cero y volvía a cortarse: un bucle que nunca se
# destrababa, con Tero contestando "se colgó el modelo local".
_TIMEOUT_CARGA_S = 180.0

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
        self._cliente_carga = Client(timeout=_TIMEOUT_CARGA_S)
        # Una lista por turno, no una lista plana de mensajes: un turno con
        # herramientas ocupa varios mensajes (assistant con tool_calls +
        # un tool por resultado) y recortar por cantidad de mensajes podría
        # cortarlo al medio, dejando un "tool" huérfano sin la llamada que
        # lo originó. Recortando por turnos enteros eso no puede pasar.
        self._historial: list[list[dict]] = []

    def _cargado(self) -> bool:
        try:
            return any(m.model == self._modelo for m in self._cliente.ps().models)
        except Exception:
            return False

    def precargar(self) -> None:
        """Deja el modelo cargado y con el prompt del sistema ya procesado.

        Cargar el modelo no alcanza. Medido recién reiniciada la máquina:
        la carga en sí tardó 3,3s, pero después la primera consulta tiene
        que procesar el prompt del sistema más el catálogo de herramientas
        (~2300 tokens) sin nada en caché y con un tercio del modelo en CPU,
        y eso solo pasó los 10s. Las consultas siguientes son rápidas
        porque Ollama reutiliza ese prefijo ya procesado. Por eso el
        calentamiento es una consulta con el mismo system y las mismas
        herramientas, que deja el prefijo en caché, y no un prompt vacío.

        Se llama al arrancar y antes de cada consulta: Ollama descarga el
        modelo tras 5 minutos sin uso, y ahí el caché se pierde. Si el
        modelo sigue cargado, es una consulta local barata.
        """
        if self._cargado():
            return
        self._cliente_carga.chat(
            model=self._modelo,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": "hola"},
            ],
            tools=catalogo(),
            options={"num_predict": 1, "temperature": 0.2},
        )

    def _chat(self, mensajes: list, con_herramientas: bool):
        self.precargar()
        return self._cliente.chat(
            model=self._modelo,
            messages=mensajes,
            tools=catalogo() if con_herramientas else None,
            # temperature baja: menos variedad al hablar, a cambio de
            # respuestas más predecibles. Ojo con el historial de esto: se
            # bajó creyendo que el muestreo era la causa de que el modelo
            # "describiera" la tool call en vez de emitirla, y no era eso
            # (era el historial en prosa, ver _responder) -- por eso
            # bajarla nunca terminó de arreglar aquello.
            options={"num_predict": _MAX_TOKENS_RESPUESTA, "temperature": 0.2},
        )

    def responder(self, texto_usuario: str) -> str:
        respuesta_texto, mensajes_turno = self._responder(texto_usuario)
        # Solo entran al historial los turnos que usaron alguna herramienta.
        # Un turno de puro texto no aporta nada que se necesite después (el
        # historial existe para "poné metallica" -> "pausala", y eso es un
        # turno con herramienta), y en cambio puede envenenar el siguiente:
        # si Whisper escucha "Buena música." en vez de "Poné música." y el
        # modelo contesta "perfecto", ese intercambio le enseña que a un
        # pedido de música se contesta escribiendo. Medido con
        # qwen3:4b-instruct: "Poné música." sale 20/20 tool calls sin
        # historial y 13/20 detrás de ese turno -- las otras 7 veces dice en
        # voz alta "reproducir_musica_aleatoria" en vez de llamarla. Detrás
        # de un turno CON herramienta sigue 20/20, y "pausala" también.
        if any(mensaje.get("role") == "tool" for mensaje in mensajes_turno):
            self._historial.append([{"role": "user", "content": texto_usuario}, *mensajes_turno])
            self._historial = self._historial[-_TURNOS_HISTORIAL:]
        return respuesta_texto

    def _mensajes_historial(self) -> list[dict]:
        return [mensaje for turno in self._historial for mensaje in turno]

    def _responder(self, texto_usuario: str) -> tuple[str, list[dict]]:
        """Devuelve (lo que se dice en voz, los mensajes que quedan en el historial).

        El historial guarda el transcript nativo de tool calling tal cual
        (un mensaje "assistant" con `tool_calls` y un mensaje "tool" con el
        resultado), que es el formato con el que el modelo fue entrenado.

        Antes acá se fabricaba una nota en prosa ("en el turno anterior
        usaste la herramienta X y el resultado fue: Y") y eso era un bug
        grave: al repetir un pedido, el modelo leía esa nota como "a esto
        se contesta escribiendo" y respondía en prosa en vez de volver a
        llamar la herramienta. Medido con qwen3:4b-instruct sobre
        "siguiente canción" repetido: 0/12 tool calls con la nota en
        prosa, 12/12 con el transcript nativo. No dependía de cómo
        estuviera redactada la nota -- sacarle el texto del resultado
        también daba 0/12 -- así que no se arregla puliendo la frase. Las
        referencias de seguimiento ("poné metallica" -> "pausala"), que
        eran el motivo de tener historial, siguen andando 12/12.
        """
        mensajes = [
            {"role": "system", "content": PROMPT_SISTEMA},
            *self._mensajes_historial(),
            {"role": "user", "content": texto_usuario},
        ]
        vistas: set = set()
        resultados: list = []
        transcripcion: list[dict] = []

        for ronda in range(_MAX_RONDAS_HERRAMIENTAS):
            try:
                respuesta = self._chat(mensajes, con_herramientas=True)
            except Exception as error:
                texto = f"Se colgó el modelo local: {error}"
                return texto, [{"role": "assistant", "content": texto}]
            mensaje = respuesta.message
            if not mensaje.tool_calls:
                if ronda == 0:
                    # Nunca pidió ninguna herramienta: charla directa.
                    #
                    # Se evaluó (2026-09-14) y se descartó una red de
                    # seguridad acá que comparara texto_usuario contra
                    # los canales de YouTube aprendidos, para el caso en
                    # que la transcripción se coma el verbo entero del
                    # pedido ("Poné Vorterix" -> "Bueno, Bortelix", visto
                    # en vivo). El problema no es de umbral: "hola" se
                    # parece un 0.75 a "olga" (mismas cuatro letras), un
                    # puntaje idéntico al de "bortelix" contra "vorterix"
                    # -- no hay forma de distinguir ambos casos con
                    # similitud de texto simple. Probado en vivo con la
                    # red puesta: "pausa la música" abría "Parén la
                    # Mano", "dale, mirá esto" abría "Radio Mitre". Un
                    # falso positivo ahí es peor que la charla genérica
                    # de acá (le cambia lo que está mirando sin que lo
                    # haya pedido), así que se sacó -- ver BITACORA.html,
                    # 2026-09-14, para el detalle completo.
                    texto = _sin_pregunta_de_seguimiento((mensaje.content or "").strip())
                    return texto, [{"role": "assistant", "content": texto}]
                break  # ya no pide más herramientas, pasa a resumir

            ejecutadas = []
            for llamada in mensaje.tool_calls:
                clave = (llamada.function.name, tuple(sorted(llamada.function.arguments.items())))
                if clave in vistas:
                    # Qwen3 4B a veces repite la misma llamada varias veces
                    # (visto con "bajame el volumen 20%" -> 3 llamadas idénticas).
                    continue
                vistas.add(clave)
                resultado = ejecutar(llamada.function.name, llamada.function.arguments)
                print(f"  tool_call: {llamada.function.name}({llamada.function.arguments}) -> {resultado!r}")
                ejecutadas.append((llamada.function.name, dict(llamada.function.arguments), resultado))
            if not ejecutadas:
                break  # solo repeticiones de lo ya hecho, no hay nada nuevo

            # Se reconstruye el mensaje del modelo con las llamadas que de
            # verdad se ejecutaron (no las repetidas que se filtraron): así
            # cada tool_call del transcript tiene su resultado, sin huecos.
            mensaje_assistant = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": nombre, "arguments": argumentos}}
                    for nombre, argumentos, _resultado in ejecutadas
                ],
            }
            mensajes.append(mensaje_assistant)
            transcripcion.append(mensaje_assistant)
            for nombre, argumentos, resultado in ejecutadas:
                mensaje_tool = {"role": "tool", "content": resultado, "name": nombre}
                mensajes.append(mensaje_tool)
                transcripcion.append(mensaje_tool)
                resultados.append((nombre, argumentos, resultado))

        if resultados and all(
            nombre in _HERRAMIENTAS_SILENCIOSAS and not es_error(resultado)
            for nombre, _args, resultado in resultados
        ):
            return "", transcripcion

        try:
            respuesta_final = self._chat(mensajes, con_herramientas=False)
        except Exception as error:
            return f"Se colgó el modelo local: {error}", transcripcion
        texto = _sin_pregunta_de_seguimiento((respuesta_final.message.content or "").strip())
        return texto, [*transcripcion, {"role": "assistant", "content": texto}]
