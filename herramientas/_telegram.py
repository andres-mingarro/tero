"""Config de Telegram para mandar cosas al celular: helper interno, no es
una herramienta (no se decora con @herramienta).

Bot creado a mano una vez con @BotFather (gratis, sin límites para uso
personal). El token y el chat_id quedan en ~/.config/tero/telegram.json
(permisos 600, fuera del repo, nunca en git) -- el token es sensible,
cualquiera que lo tenga puede mandar mensajes como el bot.
"""

import json
from pathlib import Path

_RUTA_CONFIG = Path.home() / ".config" / "tero" / "telegram.json"


def _config() -> dict:
    if not _RUTA_CONFIG.exists():
        raise RuntimeError(
            f"Falta {_RUTA_CONFIG}: no se configuró el bot de Telegram todavía."
        )
    return json.loads(_RUTA_CONFIG.read_text())


def token() -> str:
    return _config()["token"]


def chat_id() -> int:
    return _config()["chat_id"]
