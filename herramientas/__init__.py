"""Registro de herramientas: un decorador convierte una función Python en
algo que el modelo local puede ver y llamar (esquema JSON de tool calling).

Agregar una herramienta nueva es escribir una función con docstring y tipos,
decorarla con @herramienta, e importar el módulo desde cerebro/router.py.
"""

import inspect
import typing
from typing import Callable

_HERRAMIENTAS: dict[str, Callable[..., str]] = {}
_ESQUEMAS: list[dict] = []

_TIPOS_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _esquema_parametro(tipo) -> dict:
    if typing.get_origin(tipo) is typing.Literal:
        return {"type": "string", "enum": list(typing.get_args(tipo))}
    return {"type": _TIPOS_JSON.get(tipo, "string")}


def herramienta(func: Callable[..., str]) -> Callable[..., str]:
    """Registra `func` como herramienta disponible para el modelo."""
    firma = inspect.signature(func)
    tipos = typing.get_type_hints(func)
    propiedades = {}
    requeridos = []
    for nombre, parametro in firma.parameters.items():
        propiedades[nombre] = _esquema_parametro(tipos.get(nombre, str))
        if parametro.default is inspect.Parameter.empty:
            requeridos.append(nombre)

    _HERRAMIENTAS[func.__name__] = func
    _ESQUEMAS.append(
        {
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": (func.__doc__ or "").strip(),
                "parameters": {
                    "type": "object",
                    "properties": propiedades,
                    "required": requeridos,
                },
            },
        }
    )
    return func


def catalogo() -> list[dict]:
    """Esquemas JSON de todas las herramientas registradas, para pasarle a Ollama."""
    return list(_ESQUEMAS)


_PREFIJOS_ERROR = ("La herramienta ", "Herramienta desconocida")


def es_error(resultado: str) -> bool:
    """True si `resultado` es uno de los mensajes de error que arma `ejecutar`."""
    return resultado.startswith(_PREFIJOS_ERROR)


def ejecutar(nombre: str, argumentos: dict) -> str:
    """Corre la herramienta `nombre` con los argumentos que decidió el modelo."""
    func = _HERRAMIENTAS.get(nombre)
    if func is None:
        return f"Herramienta desconocida: {nombre!r}."
    try:
        return func(**argumentos)
    except Exception as error:
        return f"La herramienta {nombre!r} falló: {error}"
