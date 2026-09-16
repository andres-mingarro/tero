#!/usr/bin/env bash
# Instalador de Tero. Pensado para correrse una vez, en un escritorio
# Linux con apt (Ubuntu/Debian) y systemd/PipeWire.
#
# Cada paso explica POR QUÉ hace falta antes de instalar nada -- la idea
# es que si algún día se descarta el proyecto, quede claro qué se puede
# desinstalar y por qué se instaló en primer lugar (ver INSTALACIONES.md
# para el detalle completo, este script es la versión ejecutable de eso).
#
# Los pasos de Spotify y Telegram necesitan cuentas/acciones manuales
# (crear una app, crear un bot) que no se pueden scriptear -- el
# instalador se detiene ahí y te dice exactamente qué hacer.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

pausa() {
    echo
    read -rp "Presioná Enter para continuar (Ctrl+C para salir)... "
}

seccion() {
    echo
    echo "=================================================================="
    echo "  $1"
    echo "=================================================================="
}

# ---------------------------------------------------------------------
seccion "1/11 — uv (gestor de Python)"
echo "Fija Python 3.12 sin tocar el Python del sistema: faster-whisper y"
echo "evdev no siempre tienen wheels para versiones de Python muy nuevas."
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "Ya está instalado (uv $(uv --version))."
fi

# ---------------------------------------------------------------------
seccion "2/11 — Dependencias de Python"
echo "Todo esto vive en .venv/, no toca el sistema. Se borra solo si"
echo "borrás la carpeta del proyecto."
uv sync

# ---------------------------------------------------------------------
seccion "3/11 — Paquetes de sistema"
cat << 'EOF'
  libportaudio2       lib nativa que necesita sounddevice para grabar/
                      reproducir audio (no viene en el wheel de PyPI)
  playerctl           control de reproducción (play/pausa/siguiente) vía
                      MPRIS -- lo usa la herramienta control_media
  wmctrl              posiciona y fija "siempre encima" la ventana
                      dedicada de YouTube
EOF
pausa
sudo apt install -y libportaudio2 playerctl wmctrl

# ---------------------------------------------------------------------
seccion "4/11 — Grupo 'input' (permiso de teclado)"
echo "evdev necesita leer /dev/input/event* sin ser root, para la tecla"
echo "global de activación (push-to-talk)."
if groups "$USER" | grep -qw input; then
    echo "Ya estás en el grupo input."
else
    sudo usermod -aG input "$USER"
    echo
    echo "*** Agregado al grupo 'input'. Hace falta un LOGOUT/LOGIN"
    echo "*** completo del escritorio (no alcanza con una terminal nueva)"
    echo "*** para que tome efecto. Si después de reloguear 'id' no"
    echo "*** muestra 'input', probá un reboot completo."
fi

# ---------------------------------------------------------------------
seccion "5/11 — Ollama + modelo (el cerebro)"
echo "Servidor del modelo local. qwen3:4b-instruct (no qwen3:4b a secas:"
echo "la variante base agrega ~15-25s de razonamiento <think> a cada"
echo "respuesta, la instruct no)."
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Ollama ya está instalado."
fi
ollama pull qwen3:4b-instruct

# ---------------------------------------------------------------------
seccion "6/11 — Voz (Piper)"
echo "Modelo de voz en español (Argentina). Se descarga a voz/modelos/"
echo "(gitignored, es un binario pesado)."
uv run python -m piper.download_voices es_AR-daniela-high \
    --download-dir voz/modelos

# ---------------------------------------------------------------------
seccion "7/11 — Whisper (STT local)"
echo "No hace falta instalar nada: faster-whisper descarga el modelo"
echo "(large-v3 por defecto) solo, la primera vez que hace falta usarlo."
echo "Se puede cambiar a 'medium' o 'small' en config.toml si preferís"
echo "menos precisión a cambio de más velocidad. Con Groq configurado"
echo "(paso siguiente), esto pasa a ser solo el respaldo offline."

# ---------------------------------------------------------------------
seccion "8/11 — Groq (opcional, transcripción online)"
cat << 'EOF'
Sin esto, Whisper local hace toda la transcripción y se carga al
arrancar. Con una API key de Groq, la transcripción va primero por Groq
(mismo whisper-large-v3, gratis, sin tarjeta) y Whisper local queda de
respaldo, cargado recién si Groq falla -- libera ~3,7GB de VRAM en el uso
normal. Contrapartida: la voz sale de la máquina en cada pedido mientras
Groq esté disponible (ver CLAUDE.md, sección STT).

  1. https://console.groq.com -> crear cuenta (no pide tarjeta).
  2. Settings -> Data Controls -> activar Global ZDR (Zero Data
     Retention), para que no retengan el audio.
  3. API Keys -> generar una key y guardarla:

     mkdir -p ~/.config/tero
     read -rsp "Key de Groq: " K && printf '%s' "$K" > ~/.config/tero/groq_key
     chmod 600 ~/.config/tero/groq_key
     unset K
EOF

# ---------------------------------------------------------------------
seccion "9/11 — Soul-connector (opcional, overlay animado)"
cat << 'EOF'
Sin esto, Tero funciona igual -- el soul-connector es un cliente aparte,
opcional, la extensión de GNOME que dibuja la onda adentro de gnome-shell
(ya está en memoria, así que cuesta prácticamente nada medido).

  cd soul-connector-gnome && ./instalar.sh

Es un symlink + gnome-extensions enable. En Wayland, GNOME no relee
extensiones nuevas hasta reiniciar la sesión (logout/login) -- después
de eso queda andando solo. ./desinstalar.sh lo saca.
EOF

# ---------------------------------------------------------------------
seccion "10/11 — Spotify (opcional, para música real)"
cat << 'EOF'
Sin esto, "poné X" falla. Con esto, busca la canción real y la reproduce
(no solo abre una búsqueda en el navegador). Requiere Spotify Premium.

  1. https://developer.spotify.com/dashboard -> crear app (Web API).
  2. Redirect URI: http://127.0.0.1:8942/callback
  3. Copiá el Client ID a config.toml, sección [spotify].
  4. Corré: uv run python -m herramientas._spotify_auth
     (abre el navegador, autorizás una vez, listo)
EOF

# ---------------------------------------------------------------------
seccion "11/11 — Telegram (opcional, para mandar cosas al celular)"
cat << 'EOF'
Sin esto, "mandalo al celular" falla. Gratis, sin límites para uso
personal, no requiere OAuth ni proyecto de Google Cloud.

  1. En Telegram, hablale a @BotFather, mandale /newbot.
  2. Buscá tu bot nuevo y mandale cualquier mensaje.
  3. curl -s "https://api.telegram.org/bot<TU_TOKEN>/getUpdates"
     (buscá "chat":{"id": ...} en la respuesta)
  4. Guardá token + chat_id en ~/.config/tero/telegram.json:

     mkdir -p ~/.config/tero
     cat > ~/.config/tero/telegram.json << 'JSON'
     {"token": "TU_TOKEN", "chat_id": TU_CHAT_ID}
     JSON
     chmod 600 ~/.config/tero/telegram.json
EOF

seccion "Listo"
echo "Arrancar Tero: ./tero"
echo
echo "Ver README.md para más detalle, e INSTALACIONES.md para el registro"
echo "completo de qué se instaló y cómo revertirlo."
