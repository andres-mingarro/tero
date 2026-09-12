# Tero

Asistente de voz de escritorio, activado por tecla. Corre como daemon en
segundo plano; el micrófono se abre solo mientras se mantiene apretada una
tecla dedicada.

Desarrollo actual en **Windows**. Migración a **Linux** planificada.
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

| Pieza | Elección | Windows | Linux |
|---|---|---|---|
| Audio in/out | `sounddevice` | igual | igual |
| STT | `faster-whisper`, modelo `small`, es | igual | igual |
| Modelo local | Qwen3 4B Instruct vía Ollama | igual | igual |
| TTS | Piper (ONNX, voz es) | igual | igual |
| Tecla global | — | `pynput` | `evdev` |
| Ventana activa | — | `pygetwindow` / Win32 | `wmctrl` / D-Bus |
| Media | — | teclas multimedia | MPRIS (`playerctl`) |
| Overlay | WebView + WebSocket | igual | + `gtk4-layer-shell` |
| Servicio | — | Task Scheduler | systemd user |

Las primeras cuatro filas son idénticas en ambos sistemas: ~80% del código.

---

## Estructura

```
tero/
  main.py            bucle principal
  plataforma/        base.py, windows.py, linux.py
  voz/               stt.py, tts.py
  cerebro/           router.py, prompt.py
  herramientas/      musica.py, clima.py, web.py,
                     terminal.py, codex.py
  boca/              server.py, index.html
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
  reproducir_musica, control_media, consultar_clima,
  abrir_url, buscar_en_sitio, ajustar_volumen,
  leer_terminal, consultar_hora, capturar_pantalla,
  delegar_a_codex          # salida de escape
]
```

Catálogo completo ✅ salvo `capturar_pantalla` (fase 3, atado a
`Plataforma.capturar_pantalla`) y `delegar_a_codex` (fase 4).
`consultar_hora` no estaba en el plan original: se agregó porque el modelo
local no tiene noción de reloj y "qué hora es"/"qué día es hoy" lo
necesitan.

Notas por herramienta:

- **Clima**: Open-Meteo. Sin clave, sin registro. Leer forecast horario y
  dejar que el modelo lo resuma en lenguaje natural.
- **Media**: `reproducir_musica` abre la búsqueda en
  `open.spotify.com/search/` (no el URI `spotify:search:`: la URL web
  funciona con o sin la app instalada). `control_media` usa `playerctl`
  (MPRIS) para play/pausa/siguiente/anterior — requiere tenerlo instalado,
  no viene por defecto.
- **Web / MercadoLibre**: **no** hacer un agente con navegador. El modelo
  arma la URL y se abre. Es instantáneo y no se rompe:
  `listado.mercadolibre.com.ar/zapatillas-adidas-talle-44`
  El agente con Playwright se reserva solo para lo que no se puede
  parametrizar por URL. `buscar_en_sitio` generaliza esto a mercadolibre/
  google/youtube/amazon.
- **Terminal**: en Linux, `tmux capture-pane` si la sesión corre dentro de
  tmux; si no, portapapeles (`wl-paste`/`xclip`) — mismo mecanismo feo
  pero infalible que se iba a usar para Windows. En este entorno de
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

## La boca (overlay)

Es render, no IA. No hace falta sincronía labial ni fonemas.

- Señal: RMS del audio del TTS (volumen → apertura). Opcionalmente FFT de
  8–16 bandas para que "articule".
- **Suavizado asimétrico**: ataque rápido, decaimiento lento. Esto es lo
  que separa "se ve pro" de "se ve amateur". El RMS crudo tiembla.
- Ventana: sin bordes, siempre encima, **sin foco**, fondo transparente.
  En Linux esto requiere `gtk4-layer-shell`; sin eso el overlay roba el
  foco del editor cada vez que habla.
- Implementación: WebView renderizando un canvas HTML; el daemon manda
  niveles por WebSocket. Permite iterar el diseño recargando la página.
- Tres estados visuales distintos: **escuchando** (tecla apretada),
  **pensando** (modelo trabajando), **hablando**. Más un idle que respira.

**El daemon tiene que funcionar sin la boca.** La ventana es un cliente
opcional del stream de niveles.

---

## Latencia objetivo

Del beep a la primera sílaba, camino local:

| Etapa | Tiempo |
|---|---|
| STT (Whisper small, 5 s de audio) | 0,3–0,8 s |
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
   de punta a punta. Catálogo completo: `consultar_clima` (Open-Meteo),
   `reproducir_musica` y `control_media` (Spotify web + playerctl),
   `abrir_url` y `buscar_en_sitio` (mercadolibre/google/youtube/amazon),
   `ajustar_volumen` (wpctl/PipeWire), `leer_terminal` (tmux o
   portapapeles), `consultar_hora`. Commit `5eef997` (clima/volumen/url) +
   pendiente de commitear (música/terminal/hora, ver `git status`).
3. **Contexto** — ventana activa, portapapeles, captura bajo demanda.
   No arrancado.
4. **Codex** — la rama pesada. No arrancado.
5. **Boca** — independiente, vía WebSocket. No arrancado.
6. **Linux** — escribir `plataforma/linux.py`. Ya existe y funciona (se
   adelantó: desarrollo pasó a Linux desde el arranque del proyecto, ver
   contexto de por qué en la sección de decisiones de plataforma más
   abajo si se agrega, o preguntar — no hay `plataforma/windows.py`).

Estado actual: **fase 2 con el catálogo de herramientas cerrado**
(salvo `capturar_pantalla`, que espera a fase 3, y `delegar_a_codex`, fase
4). Pendiente para retomar:
- Commitear `herramientas/musica.py`, `herramientas/terminal.py`,
  `herramientas/tiempo.py` y los cambios en `herramientas/web.py` /
  `cerebro/router.py`.
- `control_media` necesita `playerctl` instalado (no está en este
  entorno); probarlo una vez instalado.
- No hay `tmux` instalado en este entorno, así que `leer_terminal` cae
  siempre al portapapeles — no probado el camino de tmux.
- Decidir si fase 3 (Contexto) es el próximo paso.

---

## Contexto del desarrollador

- Stack habitual: Next.js 15, TypeScript, Tailwind, Neon (PostgreSQL),
  Vercel. Este proyecto es Python, o sea territorio menos familiar.
- Preferencia por explicaciones concisas y directas.
- El overlay en WebView se eligió justamente para poder diseñar la boca con
  canvas/CSS en lugar de pelear con Cairo u OpenGL.
