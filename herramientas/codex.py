"""Delegar en Codex (ChatGPT vía su CLI oficial) tareas sobre archivos de
un proyecto real. Fase 4.

Diseño (2026-09-16): NO es un `codex exec` autónomo que corre en
background y le lee el resultado por voz al usuario -- es un **hand-off**.
Tero abre VS Code en el proyecto (para ver el diff en vivo, con la
extensión de Codex ya instalada ahí si se la quiere usar también) y una
terminal Ptyxis corriendo `codex "<tarea>"` -- la CLI oficial en modo
interactivo, con el pedido ya cargado (investigado en vivo: el panel
lateral de la extensión de VS Code no se puede precargar con un prompt
desde afuera, pero la CLI sí acepta un prompt inicial como argumento). De
ahí en más el usuario sigue solo, mirando y aprobando cada cambio con sus
propios ojos -- Codex tiene su propio control de aprobación
(`--ask-for-approval`). Esto resuelve solo el problema de la confirmación
hablada: nunca hay edición sin que el usuario la esté mirando, así que no
hace falta pedir confirmación por voz ni chequear que el repo esté limpio.

`directorio` no lo dice el usuario en voz -- pedirle al modelo local que
arme una ruta de archivo a partir de una transcripción es frágil (mismo
motivo por el que otras herramientas resuelven cosas en código en vez de
pedirle un paso de razonamiento extra al modelo, ver CLAUDE.md). Se
infiere de la terminal activa. Investigado en vivo: ni Ptyxis ni Warp
exponen su cwd actual desde afuera (sin D-Bus, sin AT-SPI, nada) -- la
única vía confiable es un hook de shell: `~/.bashrc` escribe el cwd a
`~/.cache/tero/cwd_actual` en cada prompt (`PROMPT_COMMAND`),
terminal-agnóstico. Ver INSTALACIONES.md.
"""

import shlex
import subprocess
from pathlib import Path

from herramientas import herramienta

_RUTA_CWD = Path.home() / ".cache" / "tero" / "cwd_actual"


def _directorio_activo() -> str | None:
    try:
        ruta = _RUTA_CWD.read_text().strip()
    except FileNotFoundError:
        return None
    if not ruta or not Path(ruta).is_dir():
        return None
    return ruta


@herramienta
def delegar_a_codex(tarea: str) -> str:
    """Delega en Codex (ChatGPT, vía su CLI oficial) una tarea sobre
    archivos o código de un proyecto real: leer un error, arreglar un
    bug, explicar código, refactorizar, etc. Nunca para preguntas
    generales que no tengan que ver con el proyecto en el que el usuario
    está trabajando ahora mismo -- eso se contesta directo, sin esta
    herramienta."""
    directorio = _directorio_activo()
    if directorio is None:
        return (
            "No se pudo determinar en qué proyecto está el usuario -- "
            "decile que abra una terminal en la carpeta del proyecto primero."
        )
    subprocess.Popen(["code", directorio], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # "-- codex tarea" ejecuta codex directo, sin pasar por .bashrc -- y
    # ahí es donde nvm agrega el node/codex al PATH. bash -ic sí lo carga
    # (mismo bash interactivo que abriría una pestaña común de Ptyxis).
    comando = f"codex {shlex.quote(tarea)}"
    subprocess.Popen(
        ["ptyxis", "--new-window", "-d", directorio, "--", "bash", "-ic", comando],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return f"Abrí VS Code y una terminal con Codex en {directorio}, con el pedido ya cargado."
