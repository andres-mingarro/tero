# Tero

Asistente de voz de escritorio, activado por tecla. Corre como daemon en
segundo plano; el micrófono se abre solo mientras se mantiene apretada una
tecla dedicada (por defecto, **Control derecho**).

- Push-to-talk, sin wake word.
- 80% del procesamiento corre local (Whisper, Ollama, Piper) — sin cuota,
  sin depender de internet salvo para clima/música/mapas/Telegram.
- Español rioplatense de punta a punta.

Ver [`CLAUDE.md`](CLAUDE.md) para las decisiones de diseño y el estado de
cada fase, [`BITACORA.html`](BITACORA.html) para el historial de qué se
fue haciendo, e [`INSTALACIONES.md`](INSTALACIONES.md) para el detalle de
todo lo que se instaló a nivel sistema (con cómo revertirlo).

Probado en Ubuntu con GNOME/Wayland. Debería andar en cualquier distro con
PipeWire, pero los pasos de instalación de paquetes son para `apt`.

## Instalación

Hay un instalador que hace todos los pasos de abajo, explicando qué es
cada dependencia y para qué antes de instalarla:

```bash
./install.sh
```

Los pasos de Spotify y Telegram necesitan crear una cuenta/app a mano
(no se pueden scriptear), así que el instalador se detiene ahí y te
muestra exactamente qué hacer. El resto de esta sección es la misma
información para quien prefiera ir paso a paso.

### 1. Python (vía `uv`)

El proyecto fija Python 3.12 (`faster-whisper`/`evdev` no siempre tienen
wheels para versiones de Python muy nuevas). No hace falta tener 3.12
instalado a mano, `uv` lo resuelve solo:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # si no tenés uv
uv sync
```

### 2. Paquetes de sistema

```bash
sudo apt install -y libportaudio2 playerctl wmctrl \
    libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1
```

| Paquete | Para qué |
|---|---|
| `libportaudio2` | Lib nativa de `sounddevice`, no viene en el wheel de PyPI |
| `playerctl` | Control de reproducción (play/pausa/siguiente) vía MPRIS |
| `wmctrl`, `libxcb-cursor0`, `libxcb-icccm4`, `libxcb-keysyms1` | Solo si vas a usar la boca (overlay) — ver más abajo |

### 3. Permisos de teclado

`evdev` necesita leer `/dev/input/event*` sin ser root:

```bash
sudo usermod -aG input $USER
```

**Hace falta un logout/login completo del escritorio** (no alcanza con
abrir una terminal nueva) para que el grupo nuevo tome efecto. Si después
de reloguear `id` no muestra `input`, probá un reboot completo.

### 4. Ollama + el modelo

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b-instruct
```

Se usa específicamente `qwen3:4b-instruct` y no `qwen3:4b` a secas: la
variante base agrega un bloque `<think>` a cada respuesta (15-25s de
latencia extra), la instruct no.

### 5. Voz (Piper)

```bash
uv run python -m piper.download_voices es_AR-daniela-high \
    --download-dir voz/modelos
```

### 6. Whisper (STT)

No necesita instalación aparte: `faster-whisper` descarga el modelo
(`large-v3` por defecto, configurable en `config.toml`) la primera vez que
se usa, desde HuggingFace. Con GPU tarda ~15s la descarga inicial y queda
cacheado.

### 7. Spotify (opcional, para música real)

Sin esto, los pedidos de música fallan. Con esto, `reproducir_musica`
busca la canción real y la reproduce (no solo abre una búsqueda), y
`reproducir_musica_aleatoria` elige algo nuevo de "Tus me gusta" para los
pedidos genéricos ("poné música") en vez de repetir siempre lo último.
Ambas encolan varios temas más detrás del primero, así que "siguiente"
tiene a dónde avanzar.

1. Andá a https://developer.spotify.com/dashboard, creá una app (Web API
   únicamente).
2. En **Redirect URIs**, agregá exactamente `http://127.0.0.1:8942/callback`.
3. Copiá el **Client ID** (no hace falta el secret, se usa OAuth con PKCE)
   y pegalo en `config.toml`, sección `[spotify]`.
4. Login único:

   ```bash
   uv run python -m herramientas._spotify_auth
   ```

   Se abre el navegador, autorizás, y el refresh token queda guardado en
   `~/.config/tero/spotify_token.json` (permisos 600, fuera del repo). Si
   ya habías logueado antes y el scope pedido cambió (quedó registrado en
   `INSTALACIONES.md`), borrá ese archivo y volvé a loguearte — Spotify no
   re-pregunta por permisos nuevos si la app ya estaba autorizada.

Requiere **Spotify Premium** — la Web API no deja reproducir en cuentas
free (sí deja buscar).

### 8. Telegram (opcional, para mandar cosas al celular)

1. En Telegram, hablale a **@BotFather**, mandale `/newbot` y seguí las
   instrucciones. Te da un **token**.
2. Buscá tu bot recién creado y mandale cualquier mensaje (para que
   aparezca en `getUpdates`).
3. Conseguí tu `chat_id`:

   ```bash
   curl -s "https://api.telegram.org/bot<TU_TOKEN>/getUpdates"
   ```

   Buscá `"chat":{"id": ...}` en la respuesta.
4. Guardá ambos datos en `~/.config/tero/telegram.json`:

   ```bash
   mkdir -p ~/.config/tero
   cat > ~/.config/tero/telegram.json << 'EOF'
   {
     "token": "TU_TOKEN",
     "chat_id": TU_CHAT_ID
   }
   EOF
   chmod 600 ~/.config/tero/telegram.json
   ```

### 9. Configuración (`config.toml`)

Ya viene con valores razonables. Lo más probable que quieras ajustar:

- `[tecla] nombre` — cuál tecla activa la escucha (nombre evdev, ej.
  `KEY_RIGHTCTRL`, `KEY_PAUSE`).
- `[stt] modelo` — `large-v3` (preciso, más lento) vs `medium`/`small`
  (más rápido, se equivoca más con nombres propios).

## Arrancar

```bash
uv run python main.py
```

Queda escuchando la tecla configurada. Los logs de la terminal muestran
transcripción, qué herramienta se llamó, y tiempos de cada etapa.

### La boca (overlay opcional)

Ventana flotante con una onda animada que reacciona a la voz de Tero, al
micrófono mientras escucha, y a la música de fondo, más el nombre/progreso
de lo que suena en Spotify (se oculta solo si queda pausado 30s). Es un
cliente aparte, opcional — el daemon principal funciona sin ella.

```bash
QT_QPA_PLATFORM=xcb uv run python -m boca.ventana
```

El `QT_QPA_PLATFORM=xcb` es necesario en sesiones Wayland nativas (como
GNOME): sin eso, la ventana no puede pedirle al gestor de ventanas que se
quede "siempre encima" mientras habla.

## Herramientas disponibles

`consultar_clima`, `reproducir_musica`, `reproducir_musica_aleatoria`,
`control_media`, `ajustar_volumen`,
`abrir_url`, `buscar_en_sitio` (mercadolibre/google/youtube/amazon/maps),
`calcular_viaje` (distancia y ruta entre dos lugares), `leer_terminal`,
`consultar_hora`, `mandar_al_celular`. Cada una es un archivo de ~20-80
líneas en `herramientas/` — agregar una nueva no toca el núcleo.
