# Tero

Asistente de voz de escritorio, activado por tecla. Corre como daemon en
segundo plano; el micrófono se abre solo mientras se mantiene apretada una
tecla dedicada.

Desarrollo en **Linux** (GNOME/Wayland) — el proyecto arrancó directo acá,
no hubo migración desde Windows pese a que secciones viejas de este
documento lo daban por planificado (ver `plataforma/linux.py`, ya escrito
y en uso; no existe `plataforma/windows.py`).
Idioma del asistente y del código: **español**.

---

## Qué hace

1. Se aprieta una tecla → beep → graba mientras está apretada.
2. Se suelta → beep → transcribe (local).
3. Un modelo local chico elige una herramienta y la ejecuta.
4. Responde por voz (TTS local).
5. Una ventana overlay muestra una "boca" de onda de audio mientras habla.

Ejemplos de uso previstos:

- "Poné el disco negro de Metallica"
- "¿Cómo está afuera?" → "Hace frío, a las 3 parece que va a llover"
- "Buscame zapatillas adidas talle 44 en MercadoLibre"
- "Mirá este error de la consola, ¿qué puede ser? Arreglalo"

---

## Decisiones ya tomadas (no revisar sin motivo)

### Activación: push-to-talk, sin wake word

Se descartó la escucha continua a propósito. Sin wake word no hay falsos
disparos, no hay VAD, no hay detección de fin de turno, no hay sesión de
audio abierta consumiendo cuota. La tecla marca inicio y fin.

- Pulsación sostenida = push-to-talk (grabar mientras está apretada).
- Pulsación corta = toggle (para dictados largos).
- Tecla sugerida: una que no se use nunca (`Pause`, `Menu`, `ScrollLock`)
  o `Super+Espacio`.

### Cerebro: modelo local + Codex como herramienta

**No hay un clasificador que decida entre local y nube.** El modelo local
ve el catálogo de herramientas, y una de ellas es `delegar_a_codex`. El
ruteo sale gratis del tool calling.

Regla dura: **Codex nunca contesta preguntas, solo hace trabajo sobre
archivos.** El prompt del sistema debe decirlo explícitamente. Mandarle
"¿cómo está el clima?" quema cuota y latencia.

### Restricción de presupuesto (importante)

El usuario paga ChatGPT Plus (20 USD/mes). **La suscripción no incluye
acceso a la API.** No hay presupuesto para API key de OpenAI.

La única vía oficial de usar la suscripción desde código es **Codex CLI
con login de ChatGPT** (`codex login`, después `codex exec` en modo no
interactivo). Está documentado por OpenAI.

Descartado explícitamente:

- Automatizar el navegador de ChatGPT o usar librerías no oficiales de la
  sesión web: viola los términos y arriesga la cuenta.
- Gemini: la suscripción tampoco da API, y Gemini CLI dejó de atender
  cuentas individuales el 18/06/2026 (reemplazado por Antigravity CLI).
  Fuera de alcance por decisión del usuario.

Por eso el **80% del uso diario debe correr local**: gratis, sin cuota,
sin depender de políticas de terceros.

### Contexto: bajo demanda, nunca continuo

Nada de capturar pantalla en bucle. En el instante exacto en que se aprieta
la tecla, el daemon captura:

- Ventana activa y su título (barato, siempre).
- Scrollback de terminal, si se habla de la consola.
- Captura de pantalla **solo** si se dice algo tipo "mirá esto".

Motivo de seguridad: el asistente ve terminal y pantalla, o sea contraseñas,
tokens de Vercel, conexiones a Neon, correos. No mandar eso a la nube sin
necesidad.

---

## Stack

| Pieza | Elección | Windows (nunca implementado) | Linux (real) |
|---|---|---|---|
| Audio in/out | `sounddevice` | igual | igual |
| STT | `faster-whisper`, modelo `large-v3`, es | igual | igual |
| Modelo local | Qwen3 4B Instruct vía Ollama | igual | igual |
| TTS | Piper (ONNX, voz es) | igual | igual |
| Tecla global | — | `pynput` | `evdev` |
| Ventana activa | — | `pygetwindow` / Win32 | `wmctrl` / D-Bus |
| Media | — | teclas multimedia | MPRIS (`playerctl`) + Web API de Spotify |
| Overlay | pywebview (Qt) + WebSocket | — | `QT_QPA_PLATFORM=xcb` (sin `gtk4-layer-shell`, no instalado) |
| Servicio | — | Task Scheduler | systemd user (no configurado todavía) |

`modelo small` se probó primero pero alucinaba nombres propios (Trelew,
artistas); se subió a `large-v3` a pedido explícito del usuario
("prefiero un modelo un poco más lento pero que funcione").

La fila de Windows es la tabla de diseño original — nunca se llegó a
escribir `plataforma/windows.py`, el desarrollo fue siempre en Linux.

---

## Estructura

```
tero/
  main.py            bucle principal
  plataforma/        base.py, linux.py (no hay windows.py, ver más arriba)
  voz/               stt.py, tts.py
  cerebro/           router.py, prompt.py
  herramientas/      musica.py, clima.py, web.py, mapas.py, celular.py,
                     terminal.py, tiempo.py, volumen.py,
                     _spotify_auth.py, _telegram.py, codex.py (fase 4)
  boca/              server.py, ventana.py, audio_sistema.py, index.html,
                     siriwave.umd.js (vendorizada)
  config.toml
```

### Capa de plataforma (clave para la migración)

Una sola interfaz de cinco funciones. El resto del programa nunca sabe en
qué sistema corre. Migrar a Linux = escribir un archivo de ~150 líneas.

```python
# plataforma/base.py
class Plataforma:
    def escuchar_tecla(self, on_down, on_up): ...
    def ventana_activa(self) -> dict:         ...
    def capturar_pantalla(self) -> bytes:     ...
    def media(self, accion: str):             ...
    def notificar(self, texto: str):          ...
```

El audio **no** entra en esta abstracción: `sounddevice` ya es
multiplataforma.

### Herramientas

Cada herramienta es una función con docstring y tipos. Un decorador genera
el esquema JSON que consume Ollama. Agregar una capacidad = un archivo de
~20 líneas, sin tocar el núcleo.

```python
herramientas = [
  reproducir_musica, reproducir_musica_aleatoria, control_media,
  consultar_clima, abrir_url, buscar_en_sitio, ajustar_volumen,
  leer_terminal, consultar_hora, calcular_viaje, mandar_al_celular,
  capturar_pantalla, delegar_a_codex          # salida de escape
]
```

Catálogo completo ✅ salvo `capturar_pantalla` (fase 3, atado a
`Plataforma.capturar_pantalla`) y `delegar_a_codex` (fase 4).
`consultar_hora` no estaba en el plan original: se agregó porque el modelo
local no tiene noción de reloj y "qué hora es"/"qué día es hoy" lo
necesitan. `calcular_viaje` y `mandar_al_celular` tampoco estaban en el
plan original, surgieron de pedidos concretos del usuario (distancia a un
lugar + mandarle la dirección al celular).

Notas por herramienta:

- **Clima**: Open-Meteo. Sin clave, sin registro. Leer forecast horario y
  dejar que el modelo lo resuma en lenguaje natural.
- **Música**: reproducción real vía la Web API de Spotify (OAuth PKCE, ver
  `herramientas/_spotify_auth.py`), no solo abrir una búsqueda — necesario
  para que "poné X" realmente empiece a sonar X, y para que "siguiente"
  tenga una cola de verdad detrás. `reproducir_musica` busca y encola el
  resultado más varios favoritos al azar detrás; `reproducir_musica_
  aleatoria` es para pedidos genéricos ("poné música") y elige de "Tus me
  gusta" (scope `user-library-read`) en vez de repetir siempre lo mismo.
  `control_media` usa `playerctl` (MPRIS) para play/pausa/siguiente/
  anterior sobre lo que ya esté sonando (Spotify, navegador, etc.) —
  requiere tenerlo instalado, no viene por defecto. Requiere Spotify
  Premium (la Web API no deja reproducir en cuentas free).
- **Mapas**: `calcular_viaje` geocodifica con Open-Meteo (misma API que el
  clima, sin clave) y calcula distancia/tiempo real con el servidor demo
  de OSRM (gratis, sin clave), devolviendo también la URL real de Google
  Maps para la ruta.
- **Celular**: `mandar_al_celular` manda texto/links al celular del
  usuario vía un bot de Telegram personal (`herramientas/_telegram.py`) —
  se eligió sobre GSConnect/Google Chat por simplicidad de setup.
- **Web / MercadoLibre**: **no** hacer un agente con navegador. El modelo
  arma la URL y se abre. Es instantáneo y no se rompe:
  `listado.mercadolibre.com.ar/zapatillas-adidas-talle-44`
  El agente con Playwright se reserva solo para lo que no se puede
  parametrizar por URL. `buscar_en_sitio` generaliza esto a mercadolibre/
  google/youtube/amazon/maps.
- **Terminal**: en Linux, `tmux capture-pane` si la sesión corre dentro de
  tmux; si no, portapapeles (`wl-paste`/`xclip`). En este entorno de
  desarrollo no hay tmux instalado, así que hoy el camino real es
  portapapeles.

### `delegar_a_codex`

```python
def delegar_a_codex(tarea: str, directorio: str) -> str:
    """Tareas sobre código o archivos del proyecto."""
```

Invoca `codex exec` en modo no interactivo, con `cwd` en el proyecto,
timeout generoso, salida capturada. Tres cuidados:

1. **Nunca destructivo sin confirmación.** Codex modifica archivos. Que
   corra sobre un repo con git limpio, o pedir confirmación hablada.
2. **Avisar que tarda.** El TTS dice "lo estoy viendo" al delegar, para que
   el silencio de 15 s no parezca que se colgó.
3. **Detrás de una interfaz.** Si OpenAI cambia algo, se reemplaza un solo
   archivo.

---

## La boca (overlay) ✅

Es render, no IA. No hace falta sincronía labial ni fonemas. Implementada
con `pywebview` (backend Qt/QtWebEngine — no hay PyGObject en este
entorno, así que GTK no está disponible) renderizando `boca/index.html`
(SiriWave vendorizada), y `boca/server.py` mandándole niveles/estado por
WebSocket local (`ws://127.0.0.1:8765`).

- Señal: RMS real, no solo del TTS. Tres fuentes según el estado:
  el audio del TTS mientras habla, el **micrófono en vivo** mientras
  escucha (vía el callback de `sounddevice` en `Grabador`, con auto-gain
  contra el pico reciente de volumen — un multiplicador fijo no sirve
  porque el rms de un mic vive en una escala mucho más baja e
  impredecible que la del audio de TTS), y el audio de salida del sistema
  (PipeWire, `boca/audio_sistema.py`) cuando no pasa nada más.
- **Suavizado asimétrico**: ataque rápido, decaimiento lento. Esto es lo
  que separa "se ve pro" de "se ve amateur". El RMS crudo tiembla.
- Ventana: sin bordes, sin foco. "Siempre encima" no es persistente bajo
  Mutter sin `gtk4-layer-shell` (no instalado): se fuerza
  `QT_QPA_PLATFORM=xcb` para que la ventana sea una ventana X11/XWayland
  real que `wmctrl` puede manipular, y se reintenta "traer al frente" en
  bucle mientras habla. Reposicionar por código (x/y de creación,
  `wmctrl -e`) no tiene ningún efecto en este entorno (confirmado); la
  única forma real de moverla es arrastrarla (`easy_drag=True`), y no hay
  forma de persistir esa posición entre reinicios del proceso.
- Colores por estado: la onda usa el estilo `"ios9"` de SiriWave, que
  ignora el color del constructor y trae sus curvas hardcodeadas en
  azul/rojo/verde — hay que recolorear las curvas a mano en cada cambio
  de estado (ver `aplicarEstado()` en `index.html`) para que el color
  realmente cambie, no alcanza con el `drop-shadow` de afuera.
- Cuatro estados visuales: **escuchando** (blanco, reactivo al mic),
  **pensando** (violeta claro), **hablando** (multicolor original de la
  librería — a pedido explícito del usuario, es el único estado que no
  se fuerza a un color plano), **música** (turquesa, reactivo al audio
  del sistema). Más un idle que respira.
- Debajo de la onda, nombre de la canción + barra de progreso de lo que
  suena en Spotify (polling cada ~5s, interpolado en cada frame). Se
  oculta sola si queda pausada 30s seguidos.

**El daemon tiene que funcionar sin la boca.** La ventana es un cliente
opcional del stream de niveles.

---

## Latencia objetivo

Del beep a la primera sílaba, camino local:

| Etapa | Tiempo |
|---|---|
| STT (objetivo con Whisper `small`; se subió a `large-v3` por precisión, ver Stack) | 0,3–0,8 s |
| Modelo local (tool call) | 0,3–0,6 s |
| Ejecución de herramienta | ~0 |
| TTS primer sonido (streaming) | 0,2 s |
| **Total** | **~1,5 s** |

Con GPU baja de 1 s. Solo CPU sube a 2–3 s, todavía usable. Si pasa de 4 s
se deja de usar: **medir desde el día uno.**

Camino Codex: 10–30 s. Por eso hay que avisar por voz.

---

## Seguridad

- Un agente con acceso a shell ejecutando lo que *entendió* de la voz es
  riesgo real. Un `rm` mal transcrito arruina el día.
- Whitelist estricta de comandos.
- Confirmación explícita para cualquier acción destructiva.
- Notificación con la transcripción antes de ejecutar, para ver qué entendió.

---

## Fases

1. **Esqueleto** ✅ — tecla, grabación, Whisper, TTS. Commit `630b3a4`.
2. **Cerebro** ✅ — Ollama + Qwen3 (`qwen3:4b-instruct`) con tool calling
   de punta a punta, multi-ronda (hasta 4 llamadas por turno para pedidos
   compuestos), memoria corta (últimos 2 intercambios), y varias redes de
   seguridad determinísticas en código contra fallas de sampling del
   modelo chico (ver `cerebro/router.py`: filtro de preguntas de
   seguimiento colgadas, fallback cuando el modelo dice una herramienta
   en vez de invocarla, `temperature: 0.2`). Catálogo completo (ver
   sección de Herramientas más arriba). Commits `5eef997` y `bad3ed1`.
3. **Contexto** — ventana activa, portapapeles, captura bajo demanda.
   No arrancado.
4. **Codex** — la rama pesada. No arrancado.
5. **Boca** ✅ — overlay con WebSocket, ver sección dedicada más arriba.
6. **Linux** ✅ — `plataforma/linux.py` ya existe y funciona (desarrollo
   pasó a Linux desde el arranque del proyecto; no hay
   `plataforma/windows.py`).

Estado actual: **fases 1, 2, 5 y 6 completas y commiteadas.** Pendiente
para retomar:
- Fase 3 (Contexto) es el próximo paso lógico del plan original, todavía
  sin arrancar.
- `capturar_pantalla` depende de fase 3 (`Plataforma.capturar_pantalla`).
- No hay `tmux` instalado en este entorno, así que `leer_terminal` cae
  siempre al portapapeles — no probado el camino de tmux.
- `delegar_a_codex` (fase 4) sigue sin arrancar.

---

## Contexto del desarrollador

- Stack habitual: Next.js 15, TypeScript, Tailwind, Neon (PostgreSQL),
  Vercel. Este proyecto es Python, o sea territorio menos familiar.
- Preferencia por explicaciones concisas y directas.
- El overlay en WebView se eligió justamente para poder diseñar la boca con
  canvas/CSS en lugar de pelear con Cairo u OpenGL.
