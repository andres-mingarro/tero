# Boca de Tero como extensión de GNOME Shell

La misma onda que `boca/` (pywebview), pero dibujada adentro de
gnome-shell en vez de adentro de un Chromium propio.

**Por qué:** la boca de pywebview se lleva ~1,3 GB de RAM (medido) porque
levanta QtWebEngine — un navegador entero — para dibujar una onda de
260x74. Acá el dibujo corre en el proceso de gnome-shell, que ya está en
memoria.

**Medición del reemplazo**, comparando el PSS de gnome-shell con la
extensión habilitada y deshabilitada:

| | RAM |
|---|---|
| Boca pywebview (QtWebEngine + Python) | ~1300 MB |
| Boca extensión | **por debajo del ruido de medición** (±1,4 MB) |

El proceso de gnome-shell fluctúa más de lo que cuesta la extensión.

De yapa resuelve un problema viejo: el "siempre encima" nunca funcionó
bien bajo Mutter, y la boca de pywebview lo peleaba llamando a `wmctrl` en
un bucle mientras Tero hablaba. Siendo parte del shell no hay nada que
pelear.

## Instalar

```bash
./instalar.sh
```

Hace un symlink desde `~/.local/share/gnome-shell/extensions/` a esta
carpeta del repo y habilita la extensión.

**GNOME Shell no relee las extensiones nuevas hasta reiniciarse, y en
Wayland eso significa cerrar sesión y volver a entrar.** Después de
reloguear ya queda andando sola.

## Sacarla

```bash
./desinstalar.sh
```

Y se fue. Esto está pensado así desde el diseño:

- **No toca el daemon.** La extensión es otro cliente más del WebSocket de
  niveles (`ws://127.0.0.1:8765`), el mismo que ya consume `boca/`. No hay
  una sola línea distinta en `main.py`, `boca/server.py` ni en las
  herramientas. Volver a la rama `main` no requiere deshacer nada acá.
- **No instala nada a nivel sistema.** Ni paquetes, ni servicios, ni sudo.
  Lo único que deja fuera del repo es un symlink en el home y el uuid
  anotado en dconf; `desinstalar.sh` borra las dos cosas.
- **La boca de pywebview sigue intacta.** `boca/` no se tocó: si sacás la
  extensión, `./tero` vuelve a levantarla solo (detecta si la extensión
  está habilitada y en ese caso no la levanta, para no tener dos ondas
  superpuestas).
- **Si la extensión falla, falla sola.** GNOME la deshabilita y sigue; no
  se lleva puesta la sesión.

## Archivos

| | |
|---|---|
| `extension.js` | Integración con el shell: widget flotante, estados, reproductor |
| `onda.js` | Port a Cairo del estilo `ios9` de SiriWave (la matemática de la onda) |
| `enlace.js` | Cliente WebSocket del stream de niveles del daemon |
| `stylesheet.css` | Tipografías y colores del nombre de canción y la barra |

## Desarrollo

En Wayland no se puede reiniciar gnome-shell sin cerrar sesión, así que
para probar cambios se levanta un shell anidado:

```bash
WAYLAND_DISPLAY=wayland-0 dbus-run-session -- gnome-shell --devkit
```

Ojo: en GNOME 50 la opción `--nested` ya no existe (era la de siempre en
las guías viejas); el modo anidado ahora es `--devkit`, y correr
`--wayland` sin `--display-server` intenta tomar el hardware y falla con
`Failed to take control of the session`.

Para ver si cargó bien, contra el bus de esa sesión anidada:

```bash
gdbus call --session --dest org.gnome.Shell.Extensions \
  --object-path /org/gnome/Shell/Extensions \
  --method org.gnome.Shell.Extensions.GetExtensionInfo "boca@tero.local"
```

`'state': <1.0>` es habilitada y andando; `'error'` trae el mensaje si
algo se rompió.

## Estado

Verificado en GNOME Shell 50.1:

- Carga y se habilita sin errores ni warnings de JS.
- Sobrevive un ciclo completo de disable/enable (o sea `disable()` limpia
  bien: no deja timers ni actores colgados).
- Se conecta de verdad al daemon: con Tero corriendo, el `gnome-shell`
  anidado aparece con una conexión establecida al puerto 8765 y el daemon
  loguea el cliente nuevo.
- El render de la onda se validó aparte, dibujando a PNG con Cairo: sale
  el multicolor azul/rojo/verde del original.

Lo único que **no** está verificado es cómo se ve ubicada en pantalla
dentro de la sesión real — GNOME bloquea la API de screenshot para
llamadores no autorizados, así que eso hay que mirarlo a ojo después de
reloguear.
