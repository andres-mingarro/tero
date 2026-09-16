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
5. Una ventana overlay muestra el "soul-connector", una onda de audio, mientras habla.

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
- **Barge-in**: si se aprieta la tecla mientras Tero está hablando, corta
  el audio al toque y arranca a grabar de una, sin esperar a que termine
  la frase (`main.py`, `on_down`/`voz/tts.py`). Apretarla mientras todavía
  está transcribiendo/pensando (nada sonando todavía) se ignora a
  propósito, para no tener dos turnos procesándose en paralelo.

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
con login de ChatGPT** (`codex login`, después `codex "prompt"` en modo
interactivo — Tero usa este modo, no `codex exec`, ver `delegar_a_codex`).
Está documentado por OpenAI.

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
| STT | Groq (Whisper `large-v3` online) con `faster-whisper` local de respaldo, ver más abajo | igual | igual |
| Modelo local | Qwen3 4B Instruct vía Ollama | igual | igual |
| TTS | Piper (ONNX, voz es) | igual | igual |
| Tecla global | — | `pynput` | `evdev` |
| Ventana activa | — | `pygetwindow` / Win32 | `wmctrl` / D-Bus |
| Media | — | teclas multimedia | MPRIS (`playerctl`) + Web API de Spotify |
| Overlay | pywebview (Qt) + WebSocket — deprecado 2026-09-16, ver "El soul-connector" | — | Extensión de GNOME Shell (`soul-connector-gnome/`), única implementación activa |

### STT: Groq online, Whisper local de respaldo

Decisión tomada el 2026-09-13, tras medir en vivo que un modelo chico mal
transcripto ("Poné música" → "Buena música.") le saca al cerebro toda
chance de acertar, sin importar cuán bien elija herramientas.

Con una API key de Groq en `~/.config/tero/groq_key` (ver
INSTALACIONES.md), cada pedido se transcribe primero con
`whisper-large-v3` en **Groq** (gratis, plan free, sin tarjeta): mismo
modelo y precisión que el local, sin ocupar los ~3,7 GB de VRAM que
Whisper se lleva de forma permanente. El plan gratis (2000 pedidos/día)
queda muy por encima de lo que genera un push-to-talk personal.

Whisper local (`faster-whisper`) queda como **respaldo**, cargado recién
la primera vez que Groq falla (sin key, sin red, límite alcanzado, error
del servidor) -- nunca al arrancar. Al fallar, Tero avisa por voz
("Pasando a modo offline, esperá que cargo Whisper") y sigue con el mismo
audio, sin pedir que se repita el pedido. Cuando Groq vuelve a responder,
suelta la referencia al modelo local (libera la VRAM) y avisa "Volví a
modo online". Implementado en `voz/stt.py` (`STTHibrido`). Sin key de
Groq, el comportamiento es el de siempre: 100% local, cargado al
arrancar.

**Contrapartida de privacidad, explícita:** mientras Groq esté disponible,
la voz de cada pedido sale de la máquina hacia sus servidores (no la
pantalla ni la terminal, ver más abajo). Se activó Zero Data Retention en
la cuenta para que no la retengan. Decisión del usuario, sabiendo esto —
lo que le importa proteger es la comprensión de frases libres, no el
conocimiento general del modelo (ver charla del 2026-09-13).
| Servicio | — | Task Scheduler | systemd user ✅ (`systemd/tero.service`, ver más abajo) |

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
                     terminal.py, tiempo.py, volumen.py, youtube.py,
                     mover_ventana_monitor.py, captura_pantalla.py,
                     _spotify_auth.py, _telegram.py, _ducking.py,
                     _pantalla_youtube.py, _gnome_dbus.py, codex.py
  soul_connector/    server.py, audio_sistema.py -- infraestructura
                     compartida (WebSocket + audio de sistema), no overlay;
                     el overlay en sí (pywebview) se deprecó, ver "El
                     soul-connector"
  soul-connector-gnome/  extension.js (solo visual), onda.js, barra.js,
                     particulas.js, mover.js, enlace.js, panel.js --
                     overlay activo, extensión de GNOME Shell
                     puente.js: D-Bus hacia adentro del compositor, todo
                     lo no-visual, separado a propósito — ver Reglas de
                     arquitectura
  config.toml
```

### Capa de plataforma (clave para la migración, si algún día pasa)

Interfaz mínima: solo lo que de verdad no tiene otra forma de resolverse
sin saber en qué sistema corre.

```python
# plataforma/base.py
class Plataforma:
    def escuchar_tecla(self, on_down, on_up): ...
    def notificar(self, texto: str):          ...
```

El audio **no** entra en esta abstracción: `sounddevice` ya es
multiplataforma.

Originalmente tenía cinco métodos (`ventana_activa`, `capturar_pantalla`,
`media` también) pero, con el proyecto siempre en Linux (nunca hubo
`windows.py` que justificara pasar todo por acá), ninguno de esos tres
llegó a tener un caller real — cada herramienta que necesitaba algo así
terminó llamando directo a su API real de Linux (`leer_terminal` usa
AT-SPI propio, `control_media` llama `playerctl` directo,
`capturar_pantalla` usa D-Bus a la extensión de GNOME). Se sacaron de la
interfaz el 2026-09-16 (hallazgo de la auditoría de ese día, decisión
explícita del usuario: "sacarlos, no dejarlos como fósiles"). Si el día
de mañana aparece un motivo real para portar a otro SO, se agregan de
nuevo con el uso real en mente — ver Reglas de arquitectura, "sin
abstracciones antes de tiempo".

### Herramientas

Cada herramienta es una función con docstring y tipos. Un decorador genera
el esquema JSON que consume Ollama. Agregar una capacidad = un archivo de
~20 líneas, sin tocar el núcleo.

```python
herramientas = [
  reproducir_musica, reproducir_musica_aleatoria, control_media,
  consultar_clima, abrir_url, buscar_en_sitio, ajustar_volumen,
  leer_terminal, consultar_hora, calcular_viaje, mandar_al_celular,
  reproducir_canal_youtube, abrir_youtube_general, sugerir_canales_youtube,
  capturar_pantalla, delegar_a_codex          # salida de escape
]
```

Catálogo completo ✅. `consultar_hora` no estaba en el plan original: se agregó porque el modelo
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
  **Música y YouTube se pausan mutuamente**: arrancar algo en
  `reproducir_musica`/`reproducir_musica_aleatoria` pausa la ventana de
  YouTube (Chrome expone cada ventana con media como reproductor MPRIS
  aparte, `chromium.instance<PID>` — ver `_pantalla_youtube.pausar()`),
  y `reproducir_canal_youtube` pausa Spotify puntualmente
  (`musica.pausar_spotify()`, apuntado a `-p spotify` a propósito, para
  no confundirse con el reproductor de la propia ventana de YouTube).
  **Ducking** (`herramientas/_ducking.py`, no es una herramienta del
  modelo): mientras Tero escucha/piensa/habla, **todo lo que esté
  sonando en el sistema** baja al 10% — progresivo, no de golpe, regla
  global desde el 2026-09-14 (no una lista de apps conocidas: empezó
  siendo solo Spotify, después se sumó a mano la ventana de YouTube, y
  terminó siendo "cualquier audio" a pedido explícito del usuario) — y
  vuelve solo al volumen real al terminar (no entre "pensando" y
  "hablando": si hay que hablar, se queda abajo hasta el final para no
  pegar un salto para arriba y otro para abajo antes de contestar). Se
  identifica cada stream activo (`state=="running"`) vía `pw-dump`,
  salvo el del propio proceso de Tero (TTS/beeps, por PID) para no
  duckearse a sí mismo. `Ducker` guarda a qué **PIDs** corresponde su
  estimación de volumen (no a qué ids de nodo de PipeWire, que pueden
  cambiar aunque sea la misma ventana — confirmado en vivo navegando por
  CDP) y fuerza releerlo si los PIDs activos cambiaron desde la última
  vez — sin esto, arrastraba el volumen duckeado viejo sobre un stream
  nuevo que en realidad arrancaba en su volumen real, dejándolo pegado
  bajo (bug real, visto dos veces con cambios de canal de YouTube a
  mitad de conversación). Dos vías descartadas en el camino para mover el
  volumen: `playerctl volume` no sirve porque el cliente de Spotify para
  Linux no implementa `SetVolume` vía MPRIS (éxito reportado, cero
  efecto real); la Web API de Spotify (`/me/player/volume`) sí cambia el
  volumen pero `/me/player/devices` tarda 1-3s en reflejarlo (eventual
  consistency), demasiado lento para una rampa. Lo que funciona: el
  propio volumen de cada stream de salida en PipeWire (`wpctl status` →
  "Streams", node id propio, no el sink del sistema) — instantáneo y no
  toca el sink que usa el TTS para salir.
- **Mapas**: `calcular_viaje` geocodifica con Open-Meteo (misma API que el
  clima, sin clave) y calcula distancia/tiempo real con el servidor demo
  de OSRM (gratis, sin clave), devolviendo también la URL real de Google
  Maps para la ruta.
- **Celular**: `mandar_al_celular` manda texto/links al celular del
  usuario vía un bot de Telegram personal (`herramientas/_telegram.py`) —
  se eligió sobre GSConnect/Google Chat por simplicidad de setup.
- **YouTube**: `reproducir_canal_youtube` abre en vivo el canal que el
  usuario nombre — **texto libre, no una lista fija** (`herramientas/
  youtube.py`). Empezó como un `Literal[...]` de siete canales
  hardcodeados y el usuario lo marcó como un antipatrón con razón: una
  lista cerrada no generaliza, ni el modelo puede llamar la herramienta
  con algo fuera del enum. Ahora `herramientas/_youtube_favoritos.py`
  resuelve por aprendizaje: primero busca por parecido fonético
  (`difflib`) entre los canales ya conocidos (arranca con siete
  sembrados a mano, crece con el uso) — sin red, así "Bortegui" sigue
  resolviendo a "Vorterix" aunque la transcripción salga mal, cada vez
  mejor cuantas más veces se pida; si no hay nada parecido, busca en
  vivo en YouTube (scraping de resultados filtrados a canales, sin API
  key) y lo aprende para la próxima. Persistido en
  `~/.config/tero/youtube_canales.json`. Arranca solo con sonido gracias a
  `--autoplay-policy=no-user-gesture-required` (sin esto, Chrome bloquea
  el autoplay con sonido en un perfil sin historial de interacción, que
  es siempre el caso de este perfil dedicado). Sin canal nombrado, `abrir_youtube_general` abre la home y
  pregunta específico vs. novedades (única excepción a "nunca preguntar",
  ver `cerebro/prompt.py`); `sugerir_canales_youtube` responde esa
  pregunta chequeando en vivo (`/live` de cada canal) y, de respaldo, el
  feed RSS por si subieron algo sin estar en vivo. Se abre siempre en una
  ventana de Chrome dedicada, fija en un monitor del escritorio del
  usuario (`herramientas/_pantalla_youtube.py`) — necesita forzar
  `--ozone-platform=x11` porque el Chrome nativo de Wayland no deja
  posicionar la ventana por código (ver detalle en `BITACORA.html`,
  2026-09-14). Un cambio de canal **navega la misma pestaña por CDP**
  (`--remote-debugging-port`, solo localhost) en vez de matar la ventana
  y abrir una nueva — así el stream de audio nunca cambia de identidad
  en PipeWire, que es justo lo que rompía el ducking al cambiar de canal
  a mitad de una conversación (ver más abajo). Si CDP falla, cae a abrir
  una ventana nueva.
- **Web / MercadoLibre**: **no** hacer un agente con navegador. El modelo
  arma la URL y se abre. Es instantáneo y no se rompe:
  `listado.mercadolibre.com.ar/zapatillas-adidas-talle-44`
  El agente con Playwright se reserva solo para lo que no se puede
  parametrizar por URL. `buscar_en_sitio` generaliza esto a mercadolibre/
  google/youtube/amazon/maps.
- **Terminal**: sin tmux a propósito (el usuario no quiere cambiar cómo
  labura por esto). Se lee por **AT-SPI** (accesibilidad de escritorio,
  `herramientas/_leer_terminal_atspi.py`, corrido con el Python del
  sistema por subprocess porque PyGObject no está en el venv) si la
  ventana activa en ese instante expone un nodo de rol "terminal" — el
  caso de las terminales nativas de GTK/Qt (`ptyxis`, GNOME Terminal,
  Konsole). Verificado en vivo: funciona sin que el usuario copie nada.
  Las terminales de motor gráfico propio (Warp, Alacritty, Kitty) ni
  aparecen en el árbol de accesibilidad — probado con Warp, no aparece.
  Para esas, respaldo por **selección primaria** (`wl-paste --primary`/
  `xclip -selection primary`) — lo resaltado con el mouse, sin Ctrl+C. A
  propósito **no** se revisa el portapapeles de Ctrl+C: el usuario puede
  tener algo copiado ahí para otra cosa y no quiere que Tero se lo lleve
  puesto.
- **Captura de pantalla** (`herramientas/captura_pantalla.py`, fase 3 ✅):
  el D-Bus público `org.gnome.Shell.Screenshot` tira `AccessDenied:
  Screenshot is not allowed` a cualquier llamador externo (GNOME reciente
  lo reserva para el portal) — mismo problema de fondo que mover una
  ventana Wayland nativa desde afuera. Se resuelve igual: un método D-Bus
  propio (`CapturarPantalla`, en `soul-connector-gnome/puente.js`, ver
  Reglas de arquitectura) que usa `Shell.Screenshot` desde **adentro**
  del compositor, sin pasar por esa restricción. Guarda un PNG en `/tmp` y
  devuelve la ruta — no pasa por `plataforma/` (esa capa quedó reducida a
  solo lo que de verdad no tiene otra forma de resolverse, ver "Capa de
  plataforma": todo lo Linux-específico de `herramientas/` llama directo
  a su API real). El
  modelo local es texto puro, no interpreta la imagen — eso es trabajo de
  `delegar_a_codex` (fase 4), pasándole la ruta con el flag `-i` de
  `codex exec` (sube la imagen por la sesión de ChatGPT Plus ya logueada,
  sin API key, sin exponerla por URL — ver Restricción de presupuesto).

### `delegar_a_codex` ✅ (fase 4, 2026-09-16)

```python
def delegar_a_codex(tarea: str) -> str:
    """Tareas sobre código o archivos de un proyecto real."""
```

**No es un `codex exec` autónomo en background que le lee el resultado al
usuario por voz** — ese era el diseño original de este documento, y se
abandonó antes de escribir código porque no había forma decente de
resolver la confirmación (Codex modifica archivos, y no hay pantalla en
el flujo de voz para mostrar un diff antes de aprobar). El diseño real es
un **hand-off**, pedido explícito del usuario: Tero abre la puerta, el
usuario sigue del otro lado, mirando.

`delegar_a_codex` (`herramientas/codex.py`) hace dos cosas y devuelve el
control al toque, sin esperar nada:

1. `code <directorio>` — abre VS Code en el proyecto (con la extensión de
   Codex ya instalada ahí, si se la quiere usar además).
2. Abre una terminal **Ptyxis** en `directorio`, corriendo
   `codex "<tarea>"` — la CLI oficial, en modo interactivo, con el pedido
   ya cargado como primer mensaje. Investigado en vivo antes de
   implementar: el panel lateral de la extensión de VS Code (`openai.chatgpt`)
   no se puede precargar con un prompt desde afuera, solo sirve para
   tipear a mano; la CLI (`codex "prompt"`, distinto de `codex exec`) sí
   acepta un prompt inicial como argumento y arranca la TUI interactiva
   con eso ya cargado.

De ahí en más, el usuario sigue solo — mira y aprueba cada cambio con sus
propios ojos dentro de la sesión de Codex (que tiene su propio control de
aprobación, `--ask-for-approval`). **Esto resuelve la confirmación sin
necesidad de diseñar una:** nunca hay edición autónoma sin que el usuario
la esté mirando, así que no hace falta chequear que el repo esté git
limpio ni pedir confirmación hablada — los "tres cuidados" originales de
este documento quedan obsoletos, ya no aplican.

**`directorio` no lo dice el usuario en voz ni lo arma el modelo** —
pedirle a un modelo de 4B que arme una ruta de archivo a partir de una
transcripción es frágil, mismo motivo de fondo por el que otras
herramientas de este proyecto resuelven cosas en código en vez de pedirle
un paso de razonamiento extra al modelo (ver Reglas de arquitectura). Se
infiere del contexto: la terminal activa. Investigado en vivo antes de
implementar (con el objetivo original de leer el cwd por AT-SPI/D-Bus,
sin tocar nada del sistema):

- **Ptyxis** corre como un único proceso (`--gapplication-service`) para
  todas las ventanas; el PID que da AT-SPI de la app activa apunta a ese
  proceso compartido, no a la pestaña puntual, y cada pestaña abierta
  tiene su propio `bash` hijo sin forma de saber desde afuera cuál
  corresponde a la que tiene foco. El título de la ventana tampoco trae
  un path (Ptyxis le pone el nombre del proceso en primer plano). Por
  D-Bus (`org.gnome.Ptyxis`) solo expone la superficie genérica de
  GApplication, nada de pestañas ni cwd.
- **Warp** tampoco: su `/status` es un panel para humanos dentro de la
  propia app, sin CLI/API externa para consultarlo.
- Conclusión: ninguno de los dos expone esto desde afuera. **Solución: un
  hook de shell**, terminal-agnóstico (funciona igual en cualquiera) —
  `~/.bashrc` escribe el directorio actual a
  `~/.cache/tero/cwd_actual` en cada prompt (`PROMPT_COMMAND`), mismo
  patrón que usan iTerm2/VS Code/direnv para lo mismo. Ver
  INSTALACIONES.md para el detalle y cómo revertirlo.

**Trampa encontrada en vivo:** `ptyxis -- codex "tarea"` fallaba con
`Failed to find executable codex: No such file or directory` — Ptyxis
ejecuta el comando directo, sin pasar por `.bashrc`, y en esta máquina
`codex` lo agrega al PATH `nvm` (que se carga desde `.bashrc`). Fix:
correrlo como `bash -ic 'codex "tarea"'` — el `-i` fuerza que bash cargue
`.bashrc` igual que lo haría una pestaña común de Ptyxis abierta a mano.

**Criterio de ruteo** (`cerebro/prompt.py`): sin clasificador aparte, sale
del tool calling normal — el modelo local usa `delegar_a_codex` para
pedidos sobre archivos/código de un proyecto real (un error de la
consola, un bug, "explicame este archivo"), nunca para preguntas
generales de programación que no dependan de un proyecto puntual
("¿qué es un closure?") — esas las contesta él mismo, directo.

**Estado visual "codex"** (cian + partículas, ver soul-connector-gnome/
extension.js) ya estaba implementado desde el 2026-09-14 pero sin nada
que lo disparara -- `Cerebro` acepta un callback opcional
`on_delegar_codex`, invocado justo cuando `delegar_a_codex` se ejecuta sin
error (`cerebro/router.py`, dentro del loop de tool calls). `main.py` lo
conecta a `_codex_activado()`, que pone el estado en "codex". No hace
falta revertirlo a mano: como la herramienta es casi instantánea, el
flash dura solo hasta que `_procesar()` pasa a "hablando" para decir
"listo".

### Atajo Ctrl+Shift: hand-off directo a ChatGPT (2026-09-16)

Retomando la investigación pospuesta sobre generar imágenes por voz (ver
memoria del agente, "investigación ChatGPT imágenes"): el usuario pidió
un atajo global que abra ChatGPT directo, sin pasar por Tero para nada.
**No es una herramienta del catálogo** -- no pasa por `cerebro/router.py`,
ni por tool calling, ni por ninguna regla de cuándo usar Codex: es un
hand-off puro a nivel plataforma, exactamente como `delegar_a_codex` pero
sin ni siquiera la mediación de una transcripción. El usuario le habla a
ChatGPT con el modo de voz propio de esa web.

- `Plataforma.escuchar_tecla` (`plataforma/base.py`/`linux.py`) ahora
  acepta un tercer callback opcional, `on_atajo_chatgpt`, disparado una
  sola vez por combinación cuando se detectan Ctrl+Shift juntas (acorde,
  no una tecla puntual) -- reusa el mismo loop/selector que ya lee todos
  los teclados para el push-to-talk, sin abrir un segundo listener.
- **Solo Ctrl DERECHO**, nunca el izquierdo -- decisión explícita del
  usuario tras pensarlo en vivo: el izquierdo se usa todo el tiempo en
  shortcuts de otras apps (Ctrl+Shift+T, +N, +Esc...) y sumarlo dispararía
  el atajo por accidente en medio del uso normal de la compu. El derecho
  no lo usa nada más -- mismo motivo por el que ya es la tecla de
  push-to-talk. Efecto secundario aceptado y verificado en vivo: un toque
  de Ctrl+Shift con la mano derecha también hace sonar los dos beeps de
  push-to-talk (mismo evdev, no exclusivo, dos listeners lo ven igual) --
  inofensivo, la grabación de menos de 0,2s que resulta se descarta sola.
- `main.py._abrir_chatgpt()`: `webbrowser.open("https://chatgpt.com")` +
  un flash del estado "codex" de ~1,5s (`_flash_codex_temporal`, hilo
  aparte -- a diferencia de `_codex_activado`, acá no hay ningún turno de
  voz en curso que lo revierta solo, así que se revierte a mano, y solo
  si nada más cambió el estado mientras tanto).
- **Depurado en vivo con el usuario probando en tiempo real**: la
  detección funcionaba desde el primer intento, pero las primeras pruebas
  fallaron porque el usuario apretaba Ctrl **derecho** (su costumbre, es
  push-to-talk) mientras el código en ese momento solo escuchaba el
  izquierdo. Un script de diagnóstico (loguear cada evento de tecla crudo,
  sin lógica de acorde) confirmó que el teclado mandaba los eventos bien
  y que el problema era de qué tecla física se estaba probando, no del
  código -- ahí se destapó la razón real por la que el usuario quiere el
  derecho (el menos usado), no el izquierdo.

---

## El soul-connector (overlay) ✅

Es render, no IA. No hace falta sincronía labial ni fonemas. Única
implementación activa: `soul-connector-gnome/`, extensión de GNOME Shell
— corre adentro de `gnome-shell`, que ya está en memoria, así que cuesta
prácticamente nada (medido: por debajo del ruido de medición). Ver
`soul-connector-gnome/README.md` para el detalle completo (geometría
medida, trampas de GNOME 50, arrastre con el mouse).

Infraestructura compartida en `soul_connector/` (no es la implementación
vieja completa, sobrevivió a la deprecación de abajo porque no es render):
`server.py` sirve el WebSocket local (`ws://127.0.0.1:8765`) por el que
`main.py` manda niveles/estado, y `audio_sistema.py` lee el audio de
salida del sistema (PipeWire) para el estado "música". Ninguno de los dos
sabe ni le importa qué cliente los está escuchando.

- Señal: RMS real, no solo del TTS. Tres fuentes según el estado:
  el audio del TTS mientras habla, el **micrófono en vivo** mientras
  escucha (vía el callback de `sounddevice` en `Grabador`, con auto-gain
  contra el pico reciente de volumen — un multiplicador fijo no sirve
  porque el rms de un mic vive en una escala mucho más baja e
  impredecible que la del audio de TTS), y el audio de salida del sistema
  (PipeWire, `soul_connector/audio_sistema.py`) cuando no pasa nada más.
- **Suavizado asimétrico**: ataque rápido, decaimiento lento. Esto es lo
  que separa "se ve pro" de "se ve amateur". El RMS crudo tiembla.
- Colores por estado: la onda usa el estilo `"ios9"` de SiriWave (portado
  a Cairo en `soul-connector-gnome/onda.js`), que ignora el color del
  constructor y trae sus curvas hardcodeadas en azul/rojo/verde — hay que
  recolorear las curvas a mano en cada cambio de estado para que el color
  realmente cambie, no alcanza con un glow de afuera.
- Cinco estados visuales: **escuchando** (blanco, reactivo al mic),
  **pensando** (violeta claro), **hablando** (multicolor original de la
  librería — a pedido explícito del usuario, es el único estado que no
  se fuerza a un color plano), **música** (turquesa, reactivo al audio
  del sistema), **codex** (cian + partículas, para cuando Tero delegue en
  Codex/ChatGPT — fase 4, visual ya implementado). Más un idle que respira.
- Debajo de la onda, nombre de la canción + barra de progreso de lo que
  suena en Spotify (polling cada ~5s, interpolado en cada frame). Se
  oculta sola si queda pausada 30s seguidos.

**El daemon tiene que funcionar sin el soul-connector.** Es un cliente
opcional del stream de niveles, nunca una dependencia — ver Reglas de
arquitectura.

### Overlay pywebview: deprecado (2026-09-16)

Hasta acá hubo una segunda implementación completa del overlay,
`soul_connector/ventana.py` + `index.html` + `siriwave.umd.js`
(pywebview, backend Qt/QtWebEngine), corriendo como proceso aparte —
único motivo por el que existió: funcionaba en cualquier escritorio, no
solo GNOME. Costaba ~1,3 GB de RAM (un Chromium entero para dibujar una
onda de 260x74) contra el ruido de medición de la extensión, y mantener
dos implementaciones del mismo feature en paralelo era mantenimiento
duplicado real — nada garantizaba que se mantuvieran sincronizadas más
que la disciplina de quien editaba (ver Reglas de arquitectura, hallazgo
de la auditoría del mismo día). Se sacó del repo (`git rm`), junto con la
dependencia `pywebview[qt]` de `pyproject.toml` (se llevó 17 paquetes
transitivos de Qt/PyQt6) y la rama del launcher `./tero` que la levantaba.
El detalle de cómo estaba resuelto el "siempre encima" en Wayland sin
`gtk4-layer-shell` (`QT_QPA_PLATFORM=xcb`, `wmctrl`, arrastre con
`easy_drag`) quedó en el historial de git si hace falta retomarlo.

### `puente.js`: lo no-visual vive aparte (2026-09-16)

`soul-connector-gnome/extension.js` (la clase `SoulConnector`) es *solo*
render — nada de lógica de Tero. Dos herramientas (`mover_ventana_a_monitor`,
`capturar_pantalla`) necesitan código corriendo adentro de gnome-shell por
una razón técnica real (Wayland no deja hacer ciertas cosas desde afuera
del compositor: mover una ventana nativa, saltear el `AccessDenied` del
D-Bus de screenshot), no por elección de diseño. Ese código vive en
`soul-connector-gnome/puente.js` (clase `Puente`, expone
`org.gnome.Shell.Extensions.Tero` por D-Bus), separado del archivo de la
onda — `extension.js` solo lo instancia en `enable()`/`disable()`, igual
que ya hacía con `TeroIndicator` (panel.js). Ver Reglas de arquitectura.

---

## Servicio systemd y menú de GNOME ✅

`systemd/tero.service` (unidad de usuario, `Restart=no` a propósito —
mismo criterio que `salud.py`: avisar, no revivir solo si cortó por algo
real) envuelve `./tero` sin duplicar su lógica de arranque/logging. Se
instala con un symlink:

```
ln -s ~/proyectos/tero/systemd/tero.service ~/.config/systemd/user/tero.service
systemctl --user daemon-reload
systemctl --user start tero      # arrancar ahora
systemctl --user enable tero     # opcional: arrancar solo en cada login (no activado por defecto)
```

Si la extensión de GNOME del soul-connector está activa,
`soul-connector-gnome/panel.js` agrega un toggle "Tero" al menú rápido de
GNOME (el de WiFi/Bluetooth/etc., arriba a la derecha) con Iniciar/
Reiniciar/Cerrar/Ver log, controlando este mismo servicio por
`systemctl --user` — relee el estado real cada 4s en vez de confiar en
el último click, así que si `salud.py` corta a Tero solo o alguien lo
para desde una terminal, el toggle lo nota igual. Es una pieza aparte de
`extension.js` (se instancia en `enable()`/`disable()` junto con la
onda), no depende de que el soul-connector esté dibujándose.

---

## Latencia objetivo

Del beep a la primera sílaba, camino local:

| Etapa | Tiempo |
|---|---|
| STT (Groq online, medido: ~0,6–0,7 s; con Whisper `small` local se subió a `large-v3` por precisión, ver sección STT) | 0,3–0,8 s |
| Modelo local (tool call) | 0,3–0,6 s |
| Ejecución de herramienta | ~0 |
| TTS primer sonido (streaming) | 0,2 s |
| **Total** | **~1,5 s** |

Con GPU baja de 1 s. Solo CPU sube a 2–3 s, todavía usable. Si pasa de 4 s
se deja de usar: **medir desde el día uno.**

Camino Codex: 10–30 s. Por eso hay que avisar por voz.

---

## Salud del equipo

**Tero no puede quemar la placa de video.** El control térmico vive en el
firmware de la GPU, por debajo del sistema operativo: se frena sola
(slowdown) al llegar a su límite y se apaga sola si lo pasa. En esta
máquina (RTX 5060 Laptop, 8 GB) el límite de fábrica es 87 C, slowdown por
hardware 2 C arriba, apagado 5 C arriba. Ningún proceso puede desactivar
eso. No hace falta vigilar temperatura para proteger el hardware.

Los riesgos reales son de estabilidad de la sesión, no de hardware, y son
dos:

- **Quedarse sin RAM** — el único que puede colgar el equipo entero. Son
  14 GB, y Whisper `large-v3` + Ollama no son livianos. Con `MemAvailable`
  cerca de cero, el escritorio queda inusable swapeando bastante antes de
  que el OOM killer elija a alguien (y puede no elegir a Tero).
- **Quedarse sin VRAM** — pasó de verdad al arrancar un segundo daemon sin
  querer: murió con `CUDA out of memory`. No rompe nada, pero esta GPU
  además maneja el escritorio, así que la presión de VRAM lo pone lento.

`salud.py` vigila esto en segundo plano (cada 5 s) con un criterio simple:
**la RAM corta, lo térmico solo avisa** (cortar por temperatura sería
redundante con lo que la placa ya hace sola; solo corta si el slowdown por
hardware se sostiene ~1 min, que ya habla de un problema de ventilación).
Para lo térmico se lee `clocks_throttle_reasons.hw_thermal_slowdown` y no
un umbral en grados hardcodeado: es la placa diciendo "estoy en mi
límite", con el límite real de esa placa. Toda condición exige varias
muestras seguidas — un pico puntual no corta una conversación a la mitad.
Al cortar, avisa por `notify-send` y por el log (nunca por voz: si falta
memoria, levantar el TTS para anunciarlo la empeora) y sale con código 3,
que `./tero` distingue de una caída de verdad.

## Seguridad

- Un agente con acceso a shell ejecutando lo que *entendió* de la voz es
  riesgo real. Un `rm` mal transcrito arruina el día.
- Whitelist estricta de comandos.
- Confirmación explícita para cualquier acción destructiva.
- Notificación con la transcripción antes de ejecutar, para ver qué entendió.

---

## Reglas de arquitectura (para no volverse un zombie)

Nacieron el 2026-09-16 cuando `soul-connector-gnome/extension.js` empezó a
juntar, además de la onda, dos herramientas D-Bus sin relación entre sí
(mover ventanas, capturar pantalla). Se corrigió (ver `puente.js` arriba),
pero la tentación de ir agregando código donde sea más cómodo en el
momento va a volver a aparecer. Estas reglas están para frenarla en la
próxima herramienta, no solo en esta:

- **Cada pieza hace una sola cosa, y SOUL nunca es lógica.** El
  soul-connector (`soul-connector-gnome/extension.js`, la clase
  `SoulConnector`; `soul_connector/` es solo la infraestructura
  compartida de WebSocket/audio, no render) es render puro — dibuja la
  onda, manda su posición, y nada más. **Tero
  (el daemon) tiene que funcionar perfectamente sin SOUL** — es un cliente
  opcional del WebSocket de niveles, nunca una dependencia. Si algo tiene
  que correr adentro de gnome-shell por necesidad técnica real (no por
  comodidad), va en un archivo aparte, nunca mezclado con la clase que
  dibuja (ver `puente.js`). Antes de sumar un método nuevo ahí, primero
  confirmar que de verdad no se puede hacer desde el proceso de Tero
  (Python) — la mayoría de las cosas sí se pueden (AT-SPI, D-Bus público,
  `playerctl`, `wmctrl`); esta puerta es solo para lo que Wayland/Mutter
  bloquea desde afuera del compositor.
- **Sin abstracciones antes de tiempo.** Una herramienta nueva es un
  archivo de ~20-30 líneas en `herramientas/` (ver esa sección). No se
  factoriza nada hasta que hay un segundo caso real que lo necesite —
  `herramientas/_gnome_dbus.py` se extrajo recién cuando `capturar_pantalla`
  fue la segunda herramienta en necesitar el mismo llamado D-Bus, no
  antes. La interfaz `Plataforma` (`plataforma/base.py`) es el ejemplo de
  lo contrario: se diseñó para una migración a Windows que nunca pasó, y
  hoy la mayoría de las herramientas Linux-específicas la ignoran y
  llaman a su API real directo — no vale la pena consolidar eso sin un
  motivo real (ver nota en "Capa de plataforma").
- **Nada de listas cerradas que no generalizan** (`Literal[...]`, enums
  hardcodeados) para algo que el mundo real no tiene como lista fija —
  ver el caso de youtube_canales, que empezó como `Literal` de siete
  canales y se reescribió a aprendizaje por uso.
- **El ruteo sale del tool calling, nunca de un clasificador aparte.**
  "Esto lo resuelvo yo" vs. "esto lo mando a Codex" lo decide el modelo
  local viendo el catálogo de herramientas (`delegar_a_codex` es una
  herramienta más), no un paso previo con reglas de texto (ver Cerebro).
- **Confirmación explícita para lo destructivo o costoso**, nunca
  silencioso — ver Seguridad y los tres cuidados de `delegar_a_codex`.
- **`BITACORA.html` se mantiene, y es responsabilidad del agente, no del
  usuario.** Decisión explícita (2026-09-16, ante la pregunta de si
  convenía dejarla morir): se conserva como el diario narrado de "qué se
  fue haciendo y por qué" (distinto de `git log`, que dice *qué* cambió
  pero no el razonamiento en vivo detrás). Cualquier agente que termine
  una sesión de trabajo real en este repo (features, bugs resueltos,
  decisiones de arquitectura como esta) **tiene que agregar una entrada
  antes de cerrar** — no esperar a que el usuario lo pida. Formato:
  sección `<div class="entrada">` dentro del `<section class="dia">` de
  la fecha (crear el `<section>` si es un día nuevo), con un
  `<span class="tag">` (`feature`/`bug`/`fix`/`decisión`/`config`/`infra`/
  `commit`) y uno o más `<p>` contando el motivo y lo que se probó en
  vivo — mismo nivel de detalle que las entradas ya escritas, no un
  resumen de una línea.

### Auditoría 2026-09-16: dónde está parado el proyecto

Pedida explícitamente por el usuario ("después de tantos días de
desarrollo puede pasar que la app se transforme en un engendro") para
chequear el estado real contra estas reglas, no solo confiar en que se
están siguiendo. Veredicto: **con ~5000 líneas (3450 Python + 1600 JS),
todavía no es un engendro** — capas separadas por responsabilidad, cero
`except:` desnudos, herramientas chicas y autocontenidas. Los hallazgos
de esa auditoría, y lo que se hizo/queda con cada uno:

- **Dos implementaciones paralelas del overlay** (pywebview vs. extensión
  de GNOME) — el riesgo más real que había: nada garantizaba que se
  mantuvieran sincronizadas más que la disciplina de quien editara.
  **Resuelto el mismo día**: se deprecó pywebview (ver "El soul-connector",
  sección "Overlay pywebview: deprecado").
- **`Plataforma` (`plataforma/base.py`) sin uso real** en 3 de sus 5
  métodos (`ventana_activa`, `capturar_pantalla`, `media`). **Resuelto el
  2026-09-16**: decisión del usuario, se sacaron de la interfaz — ver
  "Capa de plataforma".
- **Cero tests automatizados.** Hay comportamientos críticos documentados
  *solo en prosa* acá (ej. "0/12 vs 12/12 tool calls" con historial en
  prosa en Fase 2, el fix de preguntas colgadas en `cerebro/router.py`, el
  bug de PIDs en `_ducking.py`), verificados una vez a mano y nunca más.
  Sin un test que lo capture, un cambio futuro puede reintroducir el mismo
  bug sin que nadie lo note hasta escucharlo fallar en vivo. **Pendiente**:
  no hace falta un framework grande, alcanza con 5-10 casos de regresión
  para lo ya medido acá.
- **`BITACORA.html` desactualizado** desde 2026-09-14 pese a comits
  posteriores. **Decisión del usuario (2026-09-16): se mantiene**, y pasa
  a ser responsabilidad del agente que trabaje en el proyecto, no del
  usuario, mantenerla al día — ver regla nueva abajo.
- **`soul-connector-gnome/extension.js` como candidato natural a
  acumular** la próxima feature visual que se apile ahí en vez de en un
  archivo propio (como ya hacen `onda.js`/`barra.js`/`particulas.js`).
  No es un problema hoy, es una advertencia para la próxima vez.

Pendiente para retomar, en orden: fase 4 (`delegar_a_codex`, siguiendo las
reglas de arriba) y los tests de regresión.

---

## Fases

1. **Esqueleto** ✅ — tecla, grabación, Whisper, TTS. Commit `630b3a4`.
2. **Cerebro** ✅ — Ollama + Qwen3 (`qwen3:4b-instruct`) con tool calling
   de punta a punta, multi-ronda (hasta 4 llamadas por turno para pedidos
   compuestos), memoria corta (últimos 2 turnos). Catálogo completo (ver
   sección de Herramientas más arriba). Commits `5eef997` y `bad3ed1`.

   **El historial va en el formato nativo de tool calling** (un mensaje
   `assistant` con `tool_calls` y uno `tool` con el resultado), y esto no
   es un detalle: antes se fabricaba una nota en prosa ("en el turno
   anterior usaste la herramienta X y el resultado fue: Y") y eso le
   enseñaba al modelo que a ese pedido se contesta *escribiendo*. Medido
   repitiendo "siguiente canción": **0/12 tool calls con la nota en
   prosa, 12/12 con el transcript nativo** — y sacarle el texto del
   resultado a la nota seguía dando 0/12, o sea no dependía de la
   redacción. Ese único bug se había estado tapando con parches de string
   en el router (detectar que el modelo "dijo" el nombre de la acción en
   vez de invocarla) que se rompían con cada frase nueva; se borraron
   todos, y las frases que los motivaban ahora pasan 8/8 sin ellos.
   Lección para el futuro: si el modelo chico empieza a portarse mal,
   sospechar primero de lo que Tero le está metiendo en el contexto,
   antes de culpar al sampling o de agregar otra regla al prompt.
3. **Contexto** ✅ — captura bajo demanda. `leer_terminal` migrado a
   AT-SPI (ver Herramientas), sin ventana activa expuesta como dato
   aparte — se usa internamente solo para saber qué está enfocado, no se
   muestra a ningún lado. `capturar_pantalla` (2026-09-16): guarda la
   captura; interpretarla queda para cuando el usuario mencione fase 4 de
   nuevo con soporte de imágenes (ver [[investigacion-chatgpt-imagenes]]
   en la memoria del agente — pospuesto a pedido explícito).
4. **Codex** ✅ (2026-09-16) — no terminó siendo "la rama pesada" del plan
   original: es un hand-off (VS Code + terminal con Codex), no un agente
   autónomo corriendo en background. Ver `delegar_a_codex` más arriba
   para el diseño completo.
5. **Soul-connector** ✅ — overlay con WebSocket, ver sección dedicada más arriba.
6. **Linux** ✅ — `plataforma/linux.py` ya existe y funciona (desarrollo
   pasó a Linux desde el arranque del proyecto; no hay
   `plataforma/windows.py`).

Estado actual: **catálogo completo, las 6 fases originales cerradas.**
Pendiente para retomar (ver auditoría de arquitectura más arriba): tests
de regresión para comportamientos ya medidos a mano, y la generación de
imágenes por voz (pospuesta, ver nota de la fase 4).

---

## Contexto del desarrollador

- Stack habitual: Next.js 15, TypeScript, Tailwind, Neon (PostgreSQL),
  Vercel. Este proyecto es Python, o sea territorio menos familiar.
- Preferencia por explicaciones concisas y directas.
- El overlay en WebView se eligió justamente para poder diseñar el
  soul-connector con canvas/CSS en lugar de pelear con Cairo u OpenGL.
