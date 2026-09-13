#!/usr/bin/env bash
# Modo desarrollo: abre un GNOME Shell anidado (una ventana con un
# escritorio entero adentro) que carga la extensión desde cero.
#
# Existe porque en la sesión real GNOME cachea el código de la extensión:
# `disable`/`enable` no lo recarga, y en Wayland la única alternativa es
# cerrar sesión. Acá cada corrida arranca limpia.
#
#   ./probar.sh           cierra el anidado anterior y abre uno nuevo
#   ./probar.sh --cerrar  lo cierra y listo
#
# Para que el soul-connector se mueva, Tero tiene que estar corriendo (./tero).
#
# Se lanza con `setsid` para que el anidado y todo lo que arrastra
# (calendar-server, notificaciones, nautilus, tracker...) queden en un
# grupo de procesos propio, y cerrar = matar ese grupo entero. Matando solo
# gnome-shell quedaban esos procesos huérfanos acumulándose en cada corrida.

set -u
DIR="${XDG_RUNTIME_DIR:-/tmp}"
LOG="$DIR/soul-connector-anidado.log"
PGID_ARCHIVO="$DIR/soul-connector-anidado.pgid"

cerrar() {
  pgid=$(cat "$PGID_ARCHIVO" 2>/dev/null)
  if [ -n "$pgid" ] && kill -0 -- "-$pgid" 2>/dev/null; then
    kill -TERM -- "-$pgid" 2>/dev/null
    for _ in 1 2 3 4 5 6; do
      kill -0 -- "-$pgid" 2>/dev/null || break
      sleep 0.5
    done
    kill -KILL -- "-$pgid" 2>/dev/null
  fi
  rm -f "$PGID_ARCHIVO"
  barrer_por_bus
}

# Matar el grupo no alcanza: servicios como gvfsd los arranca `systemd
# --user` por activación de D-Bus, así que su padre es systemd y quedan
# fuera del grupo. Lo que sí heredan es el bus del anidado, que siempre es
# un socket en /tmp/dbus-*. La sesión real usa /run/user/<uid>/bus y nunca
# cae en este filtro.
barrer_por_bus() {
  for pid in $(pgrep -u "$(id -u)"); do
    bus=$(tr '\0' '\n' 2>/dev/null <"/proc/$pid/environ" | grep '^DBUS_SESSION_BUS_ADDRESS=')
    case "$bus" in
      DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/dbus-*) kill -TERM "$pid" 2>/dev/null ;;
    esac
  done
}

cerrar
if [ "${1:-}" = "--cerrar" ]; then
  echo "Shell anidado cerrado."
  exit 0
fi

setsid env WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}" \
  dbus-run-session -- gnome-shell --devkit >"$LOG" 2>&1 &
echo $! >"$PGID_ARCHIVO"  # con setsid, el pid del hijo es el pgid del grupo

echo "Shell anidado abierto (log: $LOG)."
echo "El soul-connector aparece abajo a la derecha de esa ventana."

sleep 8
if grep -q "JS ERROR" "$LOG"; then
  echo "Ojo, errores de JS:"
  grep "JS ERROR" "$LOG" | head -5
fi
