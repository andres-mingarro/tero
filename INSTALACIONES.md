# Registro de instalaciones en el sistema

Todo lo que este proyecto agregó **fuera** del repo: paquetes de sistema,
servicios, cambios de permisos. El objetivo es poder revertir todo si el
proyecto se descarta, sin tener que recordar qué se tocó.

Lo que vive **adentro** del repo no está acá: dependencias Python van en
`pyproject.toml`/`uv.lock` y desaparecen solas al borrar la carpeta
(`uv` las instala en `.venv/`, nunca en el sistema).

Criterio a partir de ahora: antes de instalar algo, preferir lo que ya
viene en el sistema (D-Bus, portapapeles, etc.) o resolverlo en Python
puro. Instalar un paquete de sistema solo cuando de verdad no hay otra.

---

## Instalado

| Qué | Comando | Para qué | Cómo revertir |
|---|---|---|---|
| `libportaudio2` | `sudo apt install libportaudio2` | Lib nativa que necesita `sounddevice` (no viene en el wheel de PyPI para Linux) | `sudo apt remove libportaudio2` |
| Usuario en grupo `input` | `sudo usermod -aG input $USER` | Para que `evdev` lea `/dev/input/event*` sin ser root (tecla global) | `sudo gpasswd -d $USER input` (+ relogin) |
| Ollama | script oficial de instalación (`curl -fsSL https://ollama.com/install.sh \| sh`) | Servidor del modelo local (Qwen3), corre como servicio systemd (`ollama.service`, usuario/grupo propios `ollama`) | `sudo systemctl disable --now ollama`, borrar `/usr/local/bin/ollama`, `/usr/share/ollama`, `sudo userdel ollama` |
| Modelos `qwen3:4b-instruct` y `qwen3:4b` | `ollama pull qwen3:4b-instruct` | Cerebro del asistente (tool calling) | `ollama rm qwen3:4b-instruct qwen3:4b` (~5 GB en `~/.ollama/models`) |
| `playerctl` | `sudo apt install playerctl` | `control_media`: play/pausa/siguiente vía MPRIS (D-Bus) | `sudo apt remove playerctl` |
| `wmctrl` | `sudo apt install wmctrl` | La boca: pedirle a Mutter "siempre encima" (`-b add,above`) para la ventana, de forma más persistente que el flag `on_top` de Qt | `sudo apt remove wmctrl` |
| `libxcb-cursor0`, `libxcb-icccm4`, `libxcb-keysyms1` | `sudo apt install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1` | La boca: dependencias del plugin `xcb` de Qt, para correr vía `QT_QPA_PLATFORM=xcb` (XWayland) — la sesión es Wayland nativo, donde `wmctrl` no ve ninguna ventana | `sudo apt remove libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1` |

## Cuentas / apps externas registradas

No son paquetes, pero son rastro fuera del repo igual — si se descarta el
proyecto, esto queda huérfano si no se borra a mano:

| Qué | Dónde | Para qué | Cómo revertir |
|---|---|---|---|
| App "Tero" en Spotify | https://developer.spotify.com/dashboard (cuenta del usuario) | Client ID para la Web API (búsqueda + reproducción real, ver `herramientas/_spotify_auth.py`). Client ID guardado en `config.toml` (no es secreto, PKCE no lo necesita) | Borrar la app desde el dashboard |
| Token OAuth de Spotify | `~/.config/tero/spotify_token.json` (permisos 600) | Refresh token de la sesión logueada (scopes: `user-modify-playback-state`, `user-read-playback-state`, `user-library-read` -- este último para `reproducir_musica_aleatoria`, que elige de "Tus me gusta") | `rm ~/.config/tero/spotify_token.json` (+ opcionalmente revocar el acceso de la app desde la cuenta de Spotify) |
| Bot de Telegram "tero_asistente_bot" | Creado con @BotFather en la cuenta de Telegram del usuario | `mandar_al_celular`: mandar texto/links al celular (Samsung SM-A556E) del usuario, gratis, sin límites de uso personal | Borrar el bot hablándole a @BotFather (`/deletebot`), y `rm ~/.config/tero/telegram.json` |

## Evaluado y descartado (para no repetir la discusión)

- **Controlador MPRIS propio en vez de `playerctl`**: técnicamente
  posible con `dbus-next`/`jeepney` (D-Bus puro, sin paquete de sistema),
  pero no elimina la dependencia real — el reproductor (Spotify, VLC, el
  navegador) tiene que hablar D-Bus/MPRIS igual, eso no se puede
  reemplazar. Se optó por `playerctl`: paquete estándar de Linux de
  escritorio (freedesktop.org), gratis, sin red, sin cuota — no es el
  tipo de dependencia externa que preocupa en este proyecto (esa
  preocupación es sobre servicios de terceros tipo OpenAI/Gemini, no
  sobre utilidades de sistema). Reabrir esta decisión si algún día
  `playerctl` da problemas reales.

## Ya presente en el sistema, no instalado por el proyecto

Por las dudas, para no confundir con lo de arriba: `wl-paste`, `xclip` y
`notify-send` (usados por `leer_terminal` y `Plataforma.notificar`) ya
venían con el escritorio GNOME/Wayland de esta máquina — no se instalaron
para Tero.
