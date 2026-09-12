#!/usr/bin/env bash
# Instala la boca como extensión de GNOME Shell.
#
# No copia nada: hace un symlink desde ~/.local/share/gnome-shell/extensions
# a esta carpeta del repo. Así editar el código acá es editar la extensión
# instalada, y desinstalar es borrar un enlace simbólico.
#
# Todo lo que toca vive en el home del usuario. No instala paquetes de
# sistema, no toca el daemon de Tero, no necesita sudo. Ver desinstalar.sh.

set -euo pipefail

UUID="boca@tero.local"
ORIGEN="$(dirname "$(readlink -f "$0")")"
DESTINO="$HOME/.local/share/gnome-shell/extensions/$UUID"

if [ -e "$DESTINO" ] && [ ! -L "$DESTINO" ]; then
  echo "✗ Ya existe $DESTINO y no es un symlink."
  echo "  Movelo o borralo a mano antes de seguir; no lo toco por las dudas."
  exit 1
fi

mkdir -p "$(dirname "$DESTINO")"
ln -sfn "$ORIGEN" "$DESTINO"
echo "✓ Enlazada: $DESTINO -> $ORIGEN"

gnome-extensions enable "$UUID" 2>/dev/null && echo "✓ Habilitada" || {
  echo "! No se pudo habilitar todavía."
  echo "  GNOME Shell no relee las extensiones nuevas hasta reiniciarse, y en"
  echo "  Wayland eso significa cerrar sesión y volver a entrar."
  echo "  Después de reloguear: gnome-extensions enable $UUID"
  exit 0
}

echo
echo "Para sacarla: ./desinstalar.sh"
