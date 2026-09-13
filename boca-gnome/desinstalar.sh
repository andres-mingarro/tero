#!/usr/bin/env bash
# Saca la boca-extensión y deja el sistema como estaba.
#
# Lo único que esta extensión deja fuera del repo es un symlink y el uuid
# anotado en la lista de extensiones de GNOME (dconf). Esto borra las dos
# cosas. No hay paquetes de sistema que desinstalar, ni cambios en el
# daemon que revertir: la boca de pywebview (boca/) sigue intacta y
# funcionando, y volver a la rama main no requiere hacer nada acá.

set -euo pipefail

UUID="boca@tero.local"
DESTINO="$HOME/.local/share/gnome-shell/extensions/$UUID"

gnome-extensions disable "$UUID" 2>/dev/null && echo "✓ Deshabilitada" \
  || echo "  (no estaba habilitada)"

# `disable` la saca de enabled-extensions pero puede dejarla anotada en
# disabled-extensions; se limpia también para no dejar rastro en dconf.
actual=$(gsettings get org.gnome.shell disabled-extensions 2>/dev/null || echo "@as []")
if [[ "$actual" == *"$UUID"* ]]; then
  nuevo=$(python3 - "$actual" "$UUID" <<'PY'
import ast, sys
valor = sys.argv[1].removeprefix("@as ").strip()
uuid = sys.argv[2]
lista = [x for x in ast.literal_eval(valor) if x != uuid]
print("[" + ", ".join("'" + x + "'" for x in lista) + "]")
PY
)
  gsettings set org.gnome.shell disabled-extensions "$nuevo"
  echo "✓ Limpiada de dconf (disabled-extensions)"
fi

if [ -L "$DESTINO" ]; then
  rm "$DESTINO"
  echo "✓ Symlink borrado: $DESTINO"
elif [ -e "$DESTINO" ]; then
  echo "! $DESTINO existe pero no es un symlink: no lo borro por las dudas."
else
  echo "  (no había symlink)"
fi

echo
echo "Listo. El código sigue en el repo; esto solo lo desconectó de GNOME."
echo "La boca original (pywebview) no fue tocada: uv run python -m boca.ventana"
