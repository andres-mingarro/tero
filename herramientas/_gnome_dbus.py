"""Llamadas D-Bus al método custom que expone soul-connector-gnome/extension.js
(interfaz org.gnome.Shell.Extensions.Tero) para pedirle cosas al compositor
que solo se pueden hacer desde adentro del shell (ver mover_ventana_monitor.py
y captura_pantalla.py, los dos módulos que usan esto).
"""

import json
import re
import subprocess

_BUS = "org.gnome.Shell"
_PATH = "/org/gnome/Shell/Extensions/Tero"
_IFAZ = "org.gnome.Shell.Extensions.Tero"


def llamar(metodo: str, *args: str) -> dict:
    proceso = subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", _BUS,
            "--object-path", _PATH,
            "--method", f"{_IFAZ}.{metodo}",
            *args,
        ],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if proceso.returncode != 0:
        error = proceso.stderr.strip()
        if "UnknownMethod" in error or "UnknownObject" in error:
            return {
                "ok": False,
                "error": "la extensión de Tero en GNOME todavía no tiene esta "
                "función cargada (hace falta cerrar sesión y volver a entrar)",
            }
        return {"ok": False, "error": error}
    coincidencia = re.search(r"^\(\s*'(.*)'\s*,?\)\s*$", proceso.stdout.strip(), re.DOTALL)
    if not coincidencia:
        return {"ok": False, "error": f"respuesta inesperada de D-Bus: {proceso.stdout!r}"}
    return json.loads(coincidencia.group(1))
