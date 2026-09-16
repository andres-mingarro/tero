"""Capturar la pantalla completa a pedido ("mirá esto", "qué es este error").

Igual que mover_ventana_monitor.py: pedirle la captura al D-Bus público
org.gnome.Shell.Screenshot desde afuera del compositor tira
"AccessDenied: Screenshot is not allowed" -- GNOME reciente restringe ese
método a llamadas mediadas por el portal (xdg-desktop-portal), pensado
para que cada app pida permiso una vez, no para un daemon de escritorio
que dispara capturas por voz. Adentro de la extensión (soul-connector-gnome/
extension.js, código confiable del propio shell) esa restricción no
aplica: ahí se usa directo Shell.Screenshot, la clase que el propio GNOME
usa para implementar ese D-Bus, sin pasar por el chequeo de acceso.

El modelo local (qwen3:4b-instruct) es texto puro, no ve imágenes. Esta
herramienta solo saca la captura y la guarda -- interpretarla es trabajo
de delegar_a_codex (fase 4), pasándole la ruta con el flag -i de
`codex exec`, que la sube por la sesión de ChatGPT Plus ya logueada (sin
API key, sin exponer nada por URL, ver CLAUDE.md).
"""

from herramientas import herramienta
from herramientas._gnome_dbus import llamar


@herramienta
def capturar_pantalla() -> str:
    """Saca una captura de la pantalla completa. Usala cuando el usuario
    pida mirar algo en pantalla (un error, una ventana, lo que sea) y
    todavía no haya nombrado un archivo o URL concretos."""
    resultado = llamar("CapturarPantalla")
    if not resultado.get("ok"):
        return f"No pude capturar la pantalla: {resultado.get('error', 'error desconocido')}"
    return (
        f"Capturé la pantalla y la guardé en {resultado['archivo']}, pero todavía "
        "no sé interpretar imágenes -- avisale al usuario que esa parte no está lista todavía."
    )
